from __future__ import annotations

import ast
import copy
import csv
import multiprocessing as mp
import queue
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .openspiel_game import build_open_spiel_game

try:
    from tqdm.auto import tqdm
except ImportError:  # pragma: no cover - optional runtime UX dependency
    tqdm = None


def _load_torch() -> Any:
    try:
        import torch
        import torch.nn as nn
        import torch.optim as optim
        from torch.distributions import Categorical
    except ImportError as exc:  # pragma: no cover - runtime environment dependent
        raise ImportError(
            "PyTorch is not installed. Install AI deps with: uv sync --group ai"
        ) from exc
    return torch, nn, optim, Categorical


def _hash_tokens(text: str, feature_dim: int) -> list[int]:
    tokens = text.replace(";", " ").replace("|", " ").replace(",", " ").replace("=", " ").split()
    if not tokens:
        return [0]
    return [abs(hash(tok)) % feature_dim for tok in tokens]


def _safe_tuple(value: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, tuple) and len(parsed) == 2:
            return int(parsed[0]), int(parsed[1])
    except Exception:  # noqa: BLE001
        pass
    return default


def _parse_observation(observation: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in observation.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        out[key.strip()] = value.strip()
    return out


class _ActorCriticNetwork:
    def __init__(self, nn: Any, feature_dim: int, hidden_dim: int, action_dim: int):
        self.backbone = nn.Sequential(
            nn.Linear(feature_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )
        self.policy_head = nn.Linear(hidden_dim, action_dim)
        self.value_head = nn.Linear(hidden_dim, 1)

    def parameters(self):
        return list(self.backbone.parameters()) + list(self.policy_head.parameters()) + list(self.value_head.parameters())

    def to(self, device):
        self.backbone.to(device)
        self.policy_head.to(device)
        self.value_head.to(device)
        return self

    def state_dict(self):
        return {
            "backbone": self.backbone.state_dict(),
            "policy_head": self.policy_head.state_dict(),
            "value_head": self.value_head.state_dict(),
        }

    def load_state_dict(self, state_dict):
        self.backbone.load_state_dict(state_dict["backbone"])
        self.policy_head.load_state_dict(state_dict["policy_head"])
        self.value_head.load_state_dict(state_dict["value_head"])

    def __call__(self, x):
        hidden = self.backbone(x)
        return self.policy_head(hidden), self.value_head(hidden).squeeze(-1)


def _obs_to_tensor(torch: Any, observation: str, feature_dim: int, device: Any):
    vec = torch.zeros(feature_dim, dtype=torch.float32, device=device)
    fields = _parse_observation(observation)

    phase_map = {"MULLIGAN": 0, "DRAW": 0, "MAIN": 1, "GAME_OVER": 2}
    phase = fields.get("phase", "")
    if phase in phase_map:
        vec[phase_map[phase]] = 1.0

    turn = float(fields.get("turn", "0") or 0)
    vec[3] = min(1.0, turn / 50.0)
    vec[4] = float(fields.get("current", "0") or 0)

    vp0, vp1 = _safe_tuple(fields.get("vp", "(0,0)"), (0, 0))
    vec[5] = vp0 / 4.0
    vec[6] = vp1 / 4.0

    mana0, mana1 = _safe_tuple(fields.get("mana", "(0,0)"), (0, 0))
    vec[7] = mana0 / 10.0
    vec[8] = mana1 / 10.0

    deck0, deck1 = _safe_tuple(fields.get("deck_sizes", "(0,0)"), (0, 0))
    vec[9] = min(1.0, deck0 / 60.0)
    vec[10] = min(1.0, deck1 / 60.0)

    own_hand = fields.get("own_hand", "")
    vec[11] = min(1.0, len([c for c in own_hand.split(",") if c]) / 20.0) if own_hand else 0.0

    opp_hand = fields.get("opponent_hand", "")
    if opp_hand.startswith("size="):
        try:
            vec[12] = min(1.0, float(opp_hand.split("=", 1)[1]) / 20.0)
        except ValueError:
            vec[12] = 0.0
    else:
        vec[12] = min(1.0, len([c for c in opp_hand.split(",") if c]) / 20.0) if opp_hand else 0.0

    board = fields.get("board", "")
    base = 13
    for idx, part in enumerate(board.split("|")[:6]):
        if "=" not in part:
            continue
        cards = part.split("=", 1)[1]
        vec[base + idx] = min(1.0, len([c for c in cards.split(",") if c]) / 10.0)

    vec[19] = 0.0 if fields.get("pending_choice", "None") == "None" else 1.0

    hash_start = 32
    hash_dim = max(1, feature_dim - hash_start)
    for idx in _hash_tokens(observation, hash_dim):
        vec[hash_start + idx] += 1.0

    norm = torch.linalg.vector_norm(vec)
    if float(norm) > 0.0:
        vec = vec / norm
    return vec


def _sample_masked_action(torch: Any, Categorical: Any, logits: Any, legal_actions: list[int]):
    mask = torch.full_like(logits, float("-inf"))
    mask[legal_actions] = 0.0
    masked_logits = logits + mask
    dist = Categorical(logits=masked_logits)
    action = dist.sample()
    return int(action.item()), dist.log_prob(action)


def _state_child(task):
    state, action = task
    return state.child(action)


@dataclass(frozen=True)
class PairingStats:
    deck_a: str
    deck_b: str
    wins_a: int
    wins_b: int
    draws: int
    games: int

    @property
    def win_rate_a(self) -> float:
        return self.wins_a / self.games if self.games else 0.0

    @property
    def win_rate_b(self) -> float:
        return self.wins_b / self.games if self.games else 0.0


@dataclass(frozen=True)
class NeuralPolicy:
    model: Any
    device: str
    feature_dim: int
    action_dim: int
    hidden_dim: int


@dataclass(frozen=True)
class EloPoint:
    update: int
    episodes: int
    rating: float
    score_vs_previous: float


@dataclass(frozen=True)
class NeuralTrainingResult:
    policy: NeuralPolicy
    elo_history: tuple[EloPoint, ...]


def save_neural_policy(policy: NeuralPolicy, path: str | Path) -> None:
    torch, _, _, _ = _load_torch()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "feature_dim": policy.feature_dim,
            "action_dim": policy.action_dim,
            "hidden_dim": policy.hidden_dim,
            "device": policy.device,
            "model_state": policy.model.state_dict(),
        },
        out,
    )


def load_neural_policy(path: str | Path, device: str = "auto") -> NeuralPolicy:
    torch, nn, _, _ = _load_torch()
    checkpoint = torch.load(Path(path), map_location="cpu")
    resolved_device = "cuda" if (device == "auto" and torch.cuda.is_available()) else ("cpu" if device == "auto" else device)
    model = _ActorCriticNetwork(
        nn=nn,
        feature_dim=int(checkpoint["feature_dim"]),
        hidden_dim=int(checkpoint["hidden_dim"]),
        action_dim=int(checkpoint["action_dim"]),
    )
    model.load_state_dict(checkpoint["model_state"])
    model.to(torch.device(resolved_device))
    return NeuralPolicy(
        model=model,
        device=resolved_device,
        feature_dim=int(checkpoint["feature_dim"]),
        action_dim=int(checkpoint["action_dim"]),
        hidden_dim=int(checkpoint["hidden_dim"]),
    )


def _evaluate_vs_previous(
    torch: Any,
    nn: Any,
    current_model: Any,
    previous_state: Any,
    deck_a: str,
    deck_b: str,
    feature_dim: int,
    hidden_dim: int,
    action_dim: int,
    device: Any,
    seed: int,
    games: int,
    deck_pool: tuple[str, ...] | None = None,
) -> float:
    previous_model = _ActorCriticNetwork(nn=nn, feature_dim=feature_dim, hidden_dim=hidden_dim, action_dim=action_dim)
    previous_model.load_state_dict(previous_state)
    previous_model.to(device)

    score = 0.0
    rng = random.Random(seed + 77_777)
    with torch.no_grad():
        for offset in range(games):
            eval_deck_a, eval_deck_b = deck_a, deck_b
            if deck_pool is not None and len(deck_pool) >= 2:
                eval_deck_a, eval_deck_b = rng.sample(deck_pool, 2)
            game = build_open_spiel_game(seed=seed + offset, deck_a=eval_deck_a, deck_b=eval_deck_b)
            state = game.new_initial_state()
            while not state.is_terminal():
                player = state.current_player()
                legal = state.legal_actions()
                if not legal:
                    break
                obs = _obs_to_tensor(torch, state.observation_string(player), feature_dim, device)
                model = current_model if player == 0 else previous_model
                logits, _ = model(obs)
                mask = torch.full_like(logits, float("-inf"))
                mask[legal] = 0.0
                action = int(torch.argmax(logits + mask).item())
                state.apply_action(action)
            r0, r1 = state.returns() if state.is_terminal() else (0.0, 0.0)
            if r0 > r1:
                score += 1.0
            elif r0 == r1:
                score += 0.5
    return score / games if games else 0.5


def train_neural_policy(
    deck_a: str,
    deck_b: str,
    episodes: int,
    seed: int,
    lr: float = 1e-3,
    hidden_dim: int = 512,
    feature_dim: int = 4096,
    gamma: float = 1.0,
    batch_envs: int = 16,
    step_workers: int = 1,
    ppo_epochs: int = 3,
    clip_eps: float = 0.2,
    value_coef: float = 0.5,
    entropy_coef: float = 0.01,
    elo_eval_every_updates: int = 10,
    elo_eval_games: int = 8,
    elo_k: float = 24.0,
    checkpoint_path: str | None = None,
    checkpoint_every_updates: int = 25,
    elo_csv_path: str | None = None,
    resume_from: str | None = None,
    device: str = "auto",
    verbose: bool = True,
) -> NeuralTrainingResult:
    torch, nn, optim, Categorical = _load_torch()
    resolved_device = "cuda" if (device == "auto" and torch.cuda.is_available()) else ("cpu" if device == "auto" else device)
    torch_device = torch.device(resolved_device)

    action_dim = int(build_open_spiel_game(seed=seed, deck_a=deck_a, deck_b=deck_b).num_distinct_actions())
    model = _ActorCriticNetwork(nn=nn, feature_dim=feature_dim, hidden_dim=hidden_dim, action_dim=action_dim)
    if resume_from:
        resumed = load_neural_policy(resume_from, device=resolved_device)
        model.load_state_dict(resumed.model.state_dict())
    model.to(torch_device)
    optimizer = optim.Adam(model.parameters(), lr=lr)

    rng = random.Random(seed)
    total_updates = max(1, (episodes + max(1, batch_envs) - 1) // max(1, batch_envs))
    update_iter: Any = range(1, total_updates + 1)
    progress = None
    if verbose and tqdm is not None:
        progress = tqdm(update_iter, total=total_updates, desc=f"nn-train {deck_a} vs {deck_b}", leave=False)
        update_iter = progress

    rating = 1000.0
    previous_snapshot = copy.deepcopy(model.state_dict())
    elo_history: list[EloPoint] = []

    completed_episodes = 0
    for update_idx in update_iter:
        env_count = min(batch_envs, episodes - completed_episodes)
        if env_count <= 0:
            break

        states = [
            build_open_spiel_game(seed=seed + rng.randrange(1_000_000_000), deck_a=deck_a, deck_b=deck_b).new_initial_state()
            for _ in range(env_count)
        ]
        trajectories: list[list[tuple[int, Any, int, Any, float, list[int]]]] = [[] for _ in range(env_count)]

        while True:
            active = [env_idx for env_idx, st in enumerate(states) if not st.is_terminal()]
            if not active:
                break

            obs_batch: list[Any] = []
            legal_batch: list[list[int]] = []
            player_batch: list[int] = []
            active_envs: list[int] = []
            for env_idx in active:
                st = states[env_idx]
                player = st.current_player()
                legal = st.legal_actions()
                if not legal:
                    continue
                obs_batch.append(_obs_to_tensor(torch, st.observation_string(player), feature_dim, torch_device))
                legal_batch.append(legal)
                player_batch.append(player)
                active_envs.append(env_idx)

            if not obs_batch:
                break

            batch_tensor = torch.stack(obs_batch, dim=0)
            logits_batch, values_batch = model(batch_tensor)
            sampled_actions: list[int] = []
            sampled_log_probs: list[Any] = []
            for row_idx, legal in enumerate(legal_batch):
                action, log_prob = _sample_masked_action(torch, Categorical, logits_batch[row_idx], legal)
                sampled_actions.append(action)
                sampled_log_probs.append(log_prob.detach())

            for row_idx, env_idx in enumerate(active_envs):
                trajectories[env_idx].append(
                    (
                        player_batch[row_idx],
                        obs_batch[row_idx].detach(),
                        sampled_actions[row_idx],
                        sampled_log_probs[row_idx],
                        float(values_batch[row_idx].detach().item()),
                        legal_batch[row_idx],
                    )
                )

            if step_workers > 1 and len(active_envs) > 1:
                with ThreadPoolExecutor(max_workers=step_workers) as executor:
                    updated_states = list(executor.map(_state_child, [(states[env_idx], sampled_actions[row_idx]) for row_idx, env_idx in enumerate(active_envs)]))
                for row_idx, env_idx in enumerate(active_envs):
                    states[env_idx] = updated_states[row_idx]
            else:
                for row_idx, env_idx in enumerate(active_envs):
                    states[env_idx].apply_action(sampled_actions[row_idx])

        obs_tensors: list[Any] = []
        actions: list[int] = []
        old_log_probs: list[Any] = []
        returns_list: list[float] = []
        advantages: list[float] = []
        legal_masks: list[Any] = []

        for env_idx, st in enumerate(states):
            terminal_returns = st.returns() if st.is_terminal() else [0.0, 0.0]
            reward_to_go = {0: 0.0, 1: 0.0}
            for player, obs_vec, action, old_log_prob, value_estimate, legal in reversed(trajectories[env_idx]):
                reward_to_go[player] = gamma * reward_to_go[player] + float(terminal_returns[player])
                ret = reward_to_go[player]
                obs_tensors.append(obs_vec)
                actions.append(action)
                old_log_probs.append(old_log_prob)
                returns_list.append(ret)
                advantages.append(ret - value_estimate)
                mask = torch.full((action_dim,), float("-inf"), dtype=torch.float32, device=torch_device)
                mask[legal] = 0.0
                legal_masks.append(mask)

        if obs_tensors:
            obs_batch_t = torch.stack(obs_tensors).to(torch_device)
            actions_t = torch.tensor(actions, dtype=torch.long, device=torch_device)
            old_log_probs_t = torch.stack(old_log_probs).to(torch_device)
            returns_t = torch.tensor(returns_list, dtype=torch.float32, device=torch_device)
            advantages_t = torch.tensor(advantages, dtype=torch.float32, device=torch_device)
            advantages_t = (advantages_t - advantages_t.mean()) / (advantages_t.std() + 1e-8)
            legal_masks_t = torch.stack(legal_masks)

            for _ in range(max(1, ppo_epochs)):
                logits_t, values_t = model(obs_batch_t)
                masked_logits = logits_t + legal_masks_t
                dist = Categorical(logits=masked_logits)
                new_log_probs = dist.log_prob(actions_t)
                ratio = torch.exp(new_log_probs - old_log_probs_t)
                surr1 = ratio * advantages_t
                surr2 = torch.clamp(ratio, 1.0 - clip_eps, 1.0 + clip_eps) * advantages_t
                policy_loss = -torch.min(surr1, surr2).mean()
                value_loss = (values_t - returns_t).pow(2).mean()
                entropy = dist.entropy().mean()
                loss = policy_loss + value_coef * value_loss - entropy_coef * entropy
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

        completed_episodes += env_count
        current_update = int(update_idx)

        if checkpoint_path and (current_update % max(1, checkpoint_every_updates) == 0 or completed_episodes >= episodes):
            save_neural_policy(
                NeuralPolicy(
                    model=model,
                    device=resolved_device,
                    feature_dim=feature_dim,
                    action_dim=action_dim,
                    hidden_dim=hidden_dim,
                ),
                checkpoint_path,
            )

        if current_update % max(1, elo_eval_every_updates) == 0 or completed_episodes >= episodes:
            score = _evaluate_vs_previous(
                torch=torch,
                nn=nn,
                current_model=model,
                previous_state=previous_snapshot,
                deck_a=deck_a,
                deck_b=deck_b,
                feature_dim=feature_dim,
                hidden_dim=hidden_dim,
                action_dim=action_dim,
                device=torch_device,
                seed=seed + 200_000 + current_update * 97,
                games=max(1, elo_eval_games),
            )
            expected = 1.0 / (1.0 + 10.0 ** ((1000.0 - rating) / 400.0))
            rating += elo_k * (score - expected)
            point = EloPoint(update=current_update, episodes=completed_episodes, rating=rating, score_vs_previous=score)
            elo_history.append(point)
            previous_snapshot = copy.deepcopy(model.state_dict())

            if elo_csv_path:
                path = Path(elo_csv_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                write_header = not path.exists()
                with path.open("a", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["update", "episodes", "elo", "score_vs_previous"])
                    if write_header:
                        writer.writeheader()
                    writer.writerow(
                        {
                            "update": point.update,
                            "episodes": point.episodes,
                            "elo": f"{point.rating:.3f}",
                            "score_vs_previous": f"{point.score_vs_previous:.3f}",
                        }
                    )

        if progress is not None and (current_update % 5 == 0 or completed_episodes >= episodes):
            progress.set_postfix_str(f"device={resolved_device} envs={env_count} episodes={completed_episodes}/{episodes} elo={rating:.1f}")
        elif verbose and tqdm is None and (current_update % 5 == 0 or completed_episodes >= episodes):
            print(
                f"update={current_update}: device={resolved_device} envs={env_count} episodes={completed_episodes}/{episodes} elo={rating:.1f}"
            )

    return NeuralTrainingResult(
        policy=NeuralPolicy(
            model=model,
            device=resolved_device,
            feature_dim=feature_dim,
            action_dim=action_dim,
            hidden_dim=hidden_dim,
        ),
        elo_history=tuple(elo_history),
    )


def rollout_neural_policy(
    neural_policy: NeuralPolicy,
    deck_a: str,
    deck_b: str,
    game_seed: int,
) -> tuple[float, float]:
    torch, _, _, _ = _load_torch()
    game = build_open_spiel_game(seed=game_seed, deck_a=deck_a, deck_b=deck_b)
    state = game.new_initial_state()
    device = torch.device(neural_policy.device)

    with torch.no_grad():
        while not state.is_terminal():
            player = state.current_player()
            legal = state.legal_actions()
            if not legal:
                break
            obs = _obs_to_tensor(torch, state.observation_string(player), neural_policy.feature_dim, device)
            logits, _ = neural_policy.model(obs)
            mask = torch.full_like(logits, float("-inf"))
            mask[legal] = 0.0
            action = int(torch.argmax(logits + mask).item())
            state.apply_action(action)

    returns = state.returns() if state.is_terminal() else [0.0, 0.0]
    return float(returns[0]), float(returns[1])


def evaluate_neural_pairing(
    deck_a: str,
    deck_b: str,
    train_episodes: int,
    eval_games: int,
    seed: int,
    lr: float = 1e-3,
    hidden_dim: int = 512,
    feature_dim: int = 4096,
    gamma: float = 1.0,
    batch_envs: int = 16,
    step_workers: int = 1,
    ppo_epochs: int = 3,
    clip_eps: float = 0.2,
    value_coef: float = 0.5,
    entropy_coef: float = 0.01,
    device: str = "auto",
    verbose: bool = True,
) -> PairingStats:
    training = train_neural_policy(
        deck_a=deck_a,
        deck_b=deck_b,
        episodes=train_episodes,
        seed=seed,
        lr=lr,
        hidden_dim=hidden_dim,
        feature_dim=feature_dim,
        gamma=gamma,
        batch_envs=batch_envs,
        step_workers=step_workers,
        ppo_epochs=ppo_epochs,
        clip_eps=clip_eps,
        value_coef=value_coef,
        entropy_coef=entropy_coef,
        device=device,
        verbose=verbose,
    )
    policy = training.policy

    wins_a = 0
    wins_b = 0
    draws = 0
    for game_offset in range(eval_games):
        r0, r1 = rollout_neural_policy(
            neural_policy=policy,
            deck_a=deck_a,
            deck_b=deck_b,
            game_seed=seed + 10_000 + game_offset,
        )
        if r0 > r1:
            wins_a += 1
        elif r1 > r0:
            wins_b += 1
        else:
            draws += 1

    if verbose:
        print(
            f"pairing={deck_a} vs {deck_b}: wins_a={wins_a}, wins_b={wins_b}, draws={draws}, games={eval_games}, device={policy.device}"
        )

    return PairingStats(
        deck_a=deck_a,
        deck_b=deck_b,
        wins_a=wins_a,
        wins_b=wins_b,
        draws=draws,
        games=eval_games,
    )


def _create_actor_critic_model(nn: Any, feature_dim: int, hidden_dim: int, action_dim: int, state_dict: dict[str, Any], device: Any) -> Any:
    model = _ActorCriticNetwork(nn=nn, feature_dim=feature_dim, hidden_dim=hidden_dim, action_dim=action_dim)
    model.load_state_dict(state_dict)
    model.to(device)
    return model


def _share_model_parameters(model: Any) -> None:
    for param in model.backbone.parameters():
        param.data = param.data.contiguous()
        param.share_memory_()
    for param in model.policy_head.parameters():
        param.data = param.data.contiguous()
        param.share_memory_()
    for param in model.value_head.parameters():
        param.data = param.data.contiguous()
        param.share_memory_()


def _state_dict_to_cpu(state_dict: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in state_dict.items():
        if isinstance(value, dict):
            out[key] = _state_dict_to_cpu(value)
        elif hasattr(value, "detach"):
            out[key] = value.detach().cpu().contiguous()
        else:
            out[key] = copy.deepcopy(value)
    return out


def _normalize_deck_pool(deck_a: str, deck_b: str, deck_pool: tuple[str, ...] | None) -> tuple[str, ...]:
    if deck_pool is None:
        names = [deck_a, deck_b]
    else:
        names = [name.strip() for name in deck_pool if name.strip()]
    deduped = tuple(dict.fromkeys(names))
    if len(deduped) < 2:
        raise ValueError("deck_pool must contain at least two unique deck names")
    return deduped


def _async_actor_loop(
    worker_id: int,
    shared_policy_model: Any,
    result_queue: Any,
    update_queue: Any,
    episode_counter: Any,
    max_episodes: int,
    stop_flag: Any,
    seed_base: int,
    deck_pool: tuple[str, ...],
    feature_dim: int,
    hidden_dim: int,
    action_dim: int,
    gamma: float,
    league_sample_prob: float,
) -> None:
    torch, nn, _, Categorical = _load_torch()
    try:
        torch.multiprocessing.set_sharing_strategy("file_system")
    except Exception:  # noqa: BLE001
        pass
    device = torch.device("cpu")
    local_rng = random.Random(seed_base + worker_id * 100_003)
    local_opponent_pool: list[dict[str, Any]] = []

    while True:
        if stop_flag.value:
            break
        while not update_queue.empty():
            try:
                snapshot = update_queue.get_nowait()
                if snapshot is None:
                    break
                local_opponent_pool.append(snapshot)
                if len(local_opponent_pool) > 64:
                    local_opponent_pool = local_opponent_pool[-64:]
            except Exception:  # noqa: BLE001
                break

        with episode_counter.get_lock():
            if episode_counter.value >= max_episodes:
                break
            episode_id = int(episode_counter.value)
            episode_counter.value += 1

        episode_seed = seed_base + episode_id * 1009 + worker_id * 17
        opponent_state = None
        if local_opponent_pool and local_rng.random() < league_sample_prob:
            opponent_state = local_rng.choice(local_opponent_pool)

        opponent_model = None
        if opponent_state is not None:
            opponent_model = _create_actor_critic_model(
                nn=nn,
                feature_dim=feature_dim,
                hidden_dim=hidden_dim,
                action_dim=action_dim,
                state_dict=opponent_state,
                device=device,
            )

        episode_deck_a, episode_deck_b = local_rng.sample(deck_pool, 2)
        game = build_open_spiel_game(seed=episode_seed, deck_a=episode_deck_a, deck_b=episode_deck_b)
        state = game.new_initial_state()
        obs_list: list[Any] = []
        actions_list: list[int] = []
        old_log_probs_list: list[float] = []
        values_list: list[float] = []
        legal_masks_list: list[Any] = []
        invalid_episode = False

        with torch.no_grad():
            while not state.is_terminal():
                player = state.current_player()
                legal = state.legal_actions()
                if not legal:
                    break
                obs = _obs_to_tensor(torch, state.observation_string(player), feature_dim, device)

                if player == 0:
                    logits, value = shared_policy_model(obs)
                    action, log_prob = _sample_masked_action(torch, Categorical, logits, legal)
                    mask = torch.full((action_dim,), float("-inf"), dtype=torch.float32, device=device)
                    mask[legal] = 0.0
                    obs_list.append(obs.clone())
                    actions_list.append(int(action))
                    old_log_probs_list.append(float(log_prob.item()))
                    values_list.append(float(value.item()))
                    legal_masks_list.append(mask)
                else:
                    model = opponent_model if opponent_model is not None else shared_policy_model
                    logits, _ = model(obs)
                    mask = torch.full_like(logits, float("-inf"))
                    mask[legal] = 0.0
                    action = int(torch.argmax(logits + mask).item())

                try:
                    state.apply_action(action)
                except ValueError:
                    if player == 0 and obs_list:
                        # Roll back the just-recorded learner action for this invalid step.
                        obs_list.pop()
                        actions_list.pop()
                        old_log_probs_list.pop()
                        values_list.pop()
                        legal_masks_list.pop()
                    invalid_episode = True
                    break

            if invalid_episode:
                break

        returns = state.returns() if state.is_terminal() else [0.0, 0.0]
        learner_return = float(returns[0])
        running = 0.0
        returns_list: list[float] = []
        for _ in reversed(obs_list):
            running = learner_return + gamma * running
            returns_list.append(running)
        returns_list.reverse()

        if obs_list:
            obs_t = torch.stack(obs_list, dim=0).cpu().contiguous()
            actions_t = torch.tensor(actions_list, dtype=torch.long).contiguous()
            old_log_probs_t = torch.tensor(old_log_probs_list, dtype=torch.float32).contiguous()
            values_t = torch.tensor(values_list, dtype=torch.float32).contiguous()
            returns_t = torch.tensor(returns_list, dtype=torch.float32).contiguous()
            legal_masks_t = torch.stack(legal_masks_list, dim=0).cpu().contiguous()

            obs_t.share_memory_()
            actions_t.share_memory_()
            old_log_probs_t.share_memory_()
            values_t.share_memory_()
            returns_t.share_memory_()
            legal_masks_t.share_memory_()

            result_queue.put(
                {
                    "episodes": 1,
                    "obs": obs_t,
                    "actions": actions_t,
                    "old_log_probs": old_log_probs_t,
                    "values": values_t,
                    "returns": returns_t,
                    "legal_masks": legal_masks_t,
                }
            )
        else:
            result_queue.put({"episodes": 1, "obs": None})


def train_neural_policy_distributed(
    deck_a: str,
    deck_b: str,
    episodes: int,
    seed: int,
    lr: float = 1e-3,
    hidden_dim: int = 512,
    feature_dim: int = 4096,
    gamma: float = 1.0,
    num_actors: int = 8,
    episodes_per_update: int = 32,
    ppo_epochs: int = 3,
    clip_eps: float = 0.2,
    value_coef: float = 0.5,
    entropy_coef: float = 0.01,
    league_pool_size: int = 16,
    league_add_every_updates: int = 5,
    elo_eval_every_updates: int = 10,
    elo_eval_games: int = 8,
    elo_k: float = 24.0,
    checkpoint_path: str | None = None,
    checkpoint_every_updates: int = 10,
    elo_csv_path: str | None = None,
    resume_from: str | None = None,
    deck_pool: tuple[str, ...] | None = None,
    pipeline_mode: str = "shared_memory",
    league_sample_prob: float = 0.5,
    device: str = "auto",
    verbose: bool = True,
) -> NeuralTrainingResult:
    torch, nn, optim, Categorical = _load_torch()
    try:
        torch.multiprocessing.set_sharing_strategy("file_system")
    except Exception:  # noqa: BLE001
        pass
    resolved_device = "cuda" if (device == "auto" and torch.cuda.is_available()) else ("cpu" if device == "auto" else device)
    torch_device = torch.device(resolved_device)

    resolved_deck_pool = _normalize_deck_pool(deck_a=deck_a, deck_b=deck_b, deck_pool=deck_pool)
    eval_deck_a, eval_deck_b = resolved_deck_pool[0], resolved_deck_pool[1]

    action_dim = int(build_open_spiel_game(seed=seed, deck_a=eval_deck_a, deck_b=eval_deck_b).num_distinct_actions())
    model = _ActorCriticNetwork(nn=nn, feature_dim=feature_dim, hidden_dim=hidden_dim, action_dim=action_dim)
    if resume_from:
        resumed = load_neural_policy(resume_from, device=resolved_device)
        model.load_state_dict(resumed.model.state_dict())
    model.to(torch_device)
    optimizer = optim.Adam(model.parameters(), lr=lr)

    if pipeline_mode not in {"shared_memory", "queue", "rpc"}:
        raise ValueError(f"Unsupported pipeline_mode={pipeline_mode!r}. Use one of: shared_memory, queue, rpc")
    if pipeline_mode in {"queue", "rpc"} and verbose:
        print(f"pipeline_mode={pipeline_mode} currently aliases to shared_memory")

    shared_policy_model = _ActorCriticNetwork(nn=nn, feature_dim=feature_dim, hidden_dim=hidden_dim, action_dim=action_dim)
    shared_policy_model.load_state_dict(_state_dict_to_cpu(model.state_dict()))
    shared_policy_model.to(torch.device("cpu"))
    _share_model_parameters(shared_policy_model)

    ctx = mp.get_context("spawn")
    result_queue = ctx.Queue(maxsize=max(64, num_actors * 8))
    update_queues = [ctx.Queue(maxsize=max(8, episodes_per_update * 2)) for _ in range(max(1, num_actors))]
    episode_counter = ctx.Value("i", 0)
    stop_flag = ctx.Value("i", 0)

    workers = [
        ctx.Process(
            target=_async_actor_loop,
            args=(
                worker_id,
                shared_policy_model,
                result_queue,
                update_queues[worker_id],
                episode_counter,
                int(max(1, episodes)),
                stop_flag,
                seed,
                resolved_deck_pool,
                feature_dim,
                hidden_dim,
                action_dim,
                gamma,
                float(max(0.0, min(1.0, league_sample_prob))),
            ),
        )
        for worker_id in range(max(1, num_actors))
    ]
    for proc in workers:
        proc.start()

    rng = random.Random(seed)
    update_count = max(1, (episodes + max(1, episodes_per_update) - 1) // max(1, episodes_per_update))
    update_iter: Any = range(1, update_count + 1)
    progress = None
    if verbose and tqdm is not None:
        progress = tqdm(update_iter, total=update_count, desc=f"dist-train generalist ({len(resolved_deck_pool)} decks)", leave=False)
        update_iter = progress

    initial_snapshot = _state_dict_to_cpu(model.state_dict())
    for worker_queue in update_queues:
        worker_queue.put(copy.deepcopy(initial_snapshot))

    rating = 1000.0
    previous_snapshot = copy.deepcopy(model.state_dict())
    league_pool: list[dict[str, Any]] = [copy.deepcopy(initial_snapshot)]
    elo_history: list[EloPoint] = []

    completed_episodes = int(episode_counter.value)
    try:
        for update_idx in update_iter:
            target_episodes = min(episodes_per_update, episodes - completed_episodes)
            if target_episodes <= 0:
                break

            batch_obs: list[Any] = []
            batch_actions: list[Any] = []
            batch_old_log_probs: list[Any] = []
            batch_returns: list[Any] = []
            batch_values: list[Any] = []
            batch_legal_masks: list[Any] = []

            collected = 0
            empty_timeouts = 0
            while collected < target_episodes:
                try:
                    payload = result_queue.get(timeout=10)
                    empty_timeouts = 0
                except queue.Empty:
                    empty_timeouts += 1
                    alive_workers = [proc for proc in workers if proc.is_alive()]
                    dead_workers = [proc for proc in workers if (not proc.is_alive()) and proc.exitcode is not None]
                    if not alive_workers:
                        produced_episodes = int(episode_counter.value)
                        if produced_episodes >= episodes:
                            if verbose:
                                print(
                                    "finalizing partial batch after actors completed global episode budget "
                                    f"(collected={collected}/{target_episodes}, produced={produced_episodes}/{episodes})"
                                )
                            collected = target_episodes
                            break
                        dead_summary = ", ".join(f"pid={proc.pid} exit={proc.exitcode}" for proc in dead_workers) or "unknown"
                        raise RuntimeError(
                            "Distributed actors all exited while waiting for rollouts "
                            f"(collected={collected}/{target_episodes}, completed={completed_episodes}/{episodes}). "
                            f"Dead workers: {dead_summary}."
                        )
                    if empty_timeouts >= 12:
                        dead_summary = ", ".join(f"pid={proc.pid} exit={proc.exitcode}" for proc in dead_workers) or "none"
                        raise RuntimeError(
                            "Timed out waiting for actor rollouts "
                            f"(collected={collected}/{target_episodes}, completed={completed_episodes}/{episodes}, "
                            f"alive_workers={len(alive_workers)}). Dead workers: {dead_summary}."
                        )
                    continue
                collected += int(payload.get("episodes", 1))
                if payload.get("obs") is None:
                    continue
                batch_obs.append(payload["obs"].to(torch_device, non_blocking=True))
                batch_actions.append(payload["actions"].to(torch_device, non_blocking=True))
                batch_old_log_probs.append(payload["old_log_probs"].to(torch_device, non_blocking=True))
                batch_returns.append(payload["returns"].to(torch_device, non_blocking=True))
                batch_values.append(payload["values"].to(torch_device, non_blocking=True))
                batch_legal_masks.append(payload["legal_masks"].to(torch_device, non_blocking=True))

            if batch_obs:
                obs_t = torch.cat(batch_obs, dim=0)
                actions_t = torch.cat(batch_actions, dim=0)
                old_log_probs_t = torch.cat(batch_old_log_probs, dim=0)
                returns_t = torch.cat(batch_returns, dim=0)
                values_old_t = torch.cat(batch_values, dim=0)
                legal_masks_t = torch.cat(batch_legal_masks, dim=0)
                advantages_t = returns_t - values_old_t
                advantages_t = (advantages_t - advantages_t.mean()) / (advantages_t.std() + 1e-8)

                for _ in range(max(1, ppo_epochs)):
                    logits_t, values_t = model(obs_t)
                    masked_logits = logits_t + legal_masks_t
                    dist = Categorical(logits=masked_logits)
                    new_log_probs = dist.log_prob(actions_t)
                    ratio = torch.exp(new_log_probs - old_log_probs_t)
                    surr1 = ratio * advantages_t
                    surr2 = torch.clamp(ratio, 1.0 - clip_eps, 1.0 + clip_eps) * advantages_t
                    policy_loss = -torch.min(surr1, surr2).mean()
                    value_loss = (values_t - returns_t).pow(2).mean()
                    entropy = dist.entropy().mean()
                    loss = policy_loss + value_coef * value_loss - entropy_coef * entropy
                    optimizer.zero_grad()
                    loss.backward()
                    optimizer.step()

            shared_policy_model.load_state_dict(_state_dict_to_cpu(model.state_dict()))

            completed_episodes += target_episodes
            current_update = int(update_idx)

            if current_update % max(1, league_add_every_updates) == 0 or completed_episodes >= episodes:
                snapshot = _state_dict_to_cpu(model.state_dict())
                league_pool.append(copy.deepcopy(snapshot))
                if len(league_pool) > max(2, league_pool_size):
                    league_pool = league_pool[-max(2, league_pool_size) :]
                for worker_queue in update_queues:
                    worker_queue.put(copy.deepcopy(rng.choice(league_pool)))

            if checkpoint_path and (current_update % max(1, checkpoint_every_updates) == 0 or completed_episodes >= episodes):
                save_neural_policy(
                    NeuralPolicy(
                        model=model,
                        device=resolved_device,
                        feature_dim=feature_dim,
                        action_dim=action_dim,
                        hidden_dim=hidden_dim,
                    ),
                    checkpoint_path,
                )

            if current_update % max(1, elo_eval_every_updates) == 0 or completed_episodes >= episodes:
                score = _evaluate_vs_previous(
                    torch=torch,
                    nn=nn,
                    current_model=model,
                    previous_state=previous_snapshot,
                    deck_a=eval_deck_a,
                    deck_b=eval_deck_b,
                    feature_dim=feature_dim,
                    hidden_dim=hidden_dim,
                    action_dim=action_dim,
                    device=torch_device,
                    seed=seed + 500_000 + current_update * 101,
                    games=max(1, elo_eval_games),
                    deck_pool=resolved_deck_pool,
                )
                expected = 1.0 / (1.0 + 10.0 ** ((1000.0 - rating) / 400.0))
                rating += elo_k * (score - expected)
                point = EloPoint(update=current_update, episodes=completed_episodes, rating=rating, score_vs_previous=score)
                elo_history.append(point)
                previous_snapshot = copy.deepcopy(model.state_dict())

                if elo_csv_path:
                    path = Path(elo_csv_path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    write_header = not path.exists()
                    with path.open("a", encoding="utf-8", newline="") as handle:
                        writer = csv.DictWriter(handle, fieldnames=["update", "episodes", "elo", "score_vs_previous"])
                        if write_header:
                            writer.writeheader()
                        writer.writerow(
                            {
                                "update": point.update,
                                "episodes": point.episodes,
                                "elo": f"{point.rating:.3f}",
                                "score_vs_previous": f"{point.score_vs_previous:.3f}",
                            }
                        )

            if progress is not None and (current_update % 2 == 0 or completed_episodes >= episodes):
                progress.set_postfix_str(
                    f"device={resolved_device} actors={num_actors} decks={len(resolved_deck_pool)} mode=shared_memory episodes={completed_episodes}/{episodes} pool={len(league_pool)} elo={rating:.1f}"
                )
            elif verbose and tqdm is None and (current_update % 2 == 0 or completed_episodes >= episodes):
                print(
                    f"update={current_update}: device={resolved_device} actors={num_actors} decks={len(resolved_deck_pool)} mode=shared_memory episodes={completed_episodes}/{episodes} pool={len(league_pool)} elo={rating:.1f}"
                )
    finally:
        stop_flag.value = 1
        for worker_queue in update_queues:
            try:
                worker_queue.put(None)
            except Exception:  # noqa: BLE001
                pass
        for proc in workers:
            proc.join(timeout=5)
            if proc.is_alive():
                proc.terminate()

    return NeuralTrainingResult(
        policy=NeuralPolicy(
            model=model,
            device=resolved_device,
            feature_dim=feature_dim,
            action_dim=action_dim,
            hidden_dim=hidden_dim,
        ),
        elo_history=tuple(elo_history),
    )


def evaluate_neural_pairing_distributed(
    deck_a: str,
    deck_b: str,
    train_episodes: int,
    eval_games: int,
    seed: int,
    lr: float = 1e-3,
    hidden_dim: int = 512,
    feature_dim: int = 4096,
    gamma: float = 1.0,
    num_actors: int = 8,
    episodes_per_update: int = 32,
    ppo_epochs: int = 3,
    clip_eps: float = 0.2,
    value_coef: float = 0.5,
    entropy_coef: float = 0.01,
    league_pool_size: int = 16,
    league_add_every_updates: int = 5,
    pipeline_mode: str = "shared_memory",
    league_sample_prob: float = 0.5,
    device: str = "auto",
    verbose: bool = True,
) -> PairingStats:
    training = train_neural_policy_distributed(
        deck_a=deck_a,
        deck_b=deck_b,
        episodes=train_episodes,
        seed=seed,
        lr=lr,
        hidden_dim=hidden_dim,
        feature_dim=feature_dim,
        gamma=gamma,
        num_actors=num_actors,
        episodes_per_update=episodes_per_update,
        ppo_epochs=ppo_epochs,
        clip_eps=clip_eps,
        value_coef=value_coef,
        entropy_coef=entropy_coef,
        league_pool_size=league_pool_size,
        league_add_every_updates=league_add_every_updates,
        pipeline_mode=pipeline_mode,
        league_sample_prob=league_sample_prob,
        device=device,
        verbose=verbose,
    )
    policy = training.policy

    wins_a = 0
    wins_b = 0
    draws = 0
    for game_offset in range(eval_games):
        r0, r1 = rollout_neural_policy(
            neural_policy=policy,
            deck_a=deck_a,
            deck_b=deck_b,
            game_seed=seed + 10_000 + game_offset,
        )
        if r0 > r1:
            wins_a += 1
        elif r1 > r0:
            wins_b += 1
        else:
            draws += 1

    if verbose:
        print(
            f"pairing={deck_a} vs {deck_b}: wins_a={wins_a}, wins_b={wins_b}, draws={draws}, games={eval_games}, device={policy.device}, actors={num_actors}"
        )

    return PairingStats(
        deck_a=deck_a,
        deck_b=deck_b,
        wins_a=wins_a,
        wins_b=wins_b,
        draws=draws,
        games=eval_games,
    )


def _sample_action(action_probs: dict[int, float], rng: random.Random) -> int:
    roll = rng.random()
    cumulative = 0.0
    items = sorted(action_probs.items(), key=lambda x: x[0])
    for action, prob in items:
        cumulative += prob
        if roll <= cumulative:
            return action
    return items[-1][0]


def _load_algorithms() -> tuple[Any, Any]:
    try:
        from open_spiel.python.algorithms import external_sampling_mccfr
        from open_spiel.python.algorithms import outcome_sampling_mccfr
    except ImportError as exc:  # pragma: no cover - runtime environment dependent
        raise ImportError(
            "OpenSpiel is not installed in this environment. Install it with: uv pip install open_spiel"
        ) from exc
    return external_sampling_mccfr, outcome_sampling_mccfr


def build_solver(game: Any, algorithm: str):
    external_sampling_mccfr, outcome_sampling_mccfr = _load_algorithms()
    if algorithm == "external":
        return external_sampling_mccfr.ExternalSamplingSolver(game)
    if algorithm == "outcome":
        return outcome_sampling_mccfr.OutcomeSamplingSolver(game)
    raise ValueError(f"Unknown algorithm: {algorithm}")


def train_average_policy(
    deck_a: str,
    deck_b: str,
    iterations: int,
    algorithm: str,
    seed: int,
    eval_every: int,
    verbose: bool = True,
):
    game = build_open_spiel_game(seed=seed, deck_a=deck_a, deck_b=deck_b)
    solver = build_solver(game, algorithm)

    progress_iter: Any = range(1, iterations + 1)
    progress = None
    if verbose and tqdm is not None:
        progress = tqdm(progress_iter, total=iterations, desc=f"train {deck_a} vs {deck_b}", leave=False)
        progress_iter = progress

    for step in progress_iter:
        solver.iteration()
        step_num = int(step)
        if progress is not None and (step_num % eval_every == 0 or step_num == iterations):
            progress.set_postfix_str(f"{algorithm} iter={step_num}")
        if verbose and tqdm is None and (step_num % eval_every == 0 or step_num == iterations):
            print(f"iter={step_num}: updated {algorithm} mccfr policy")

    return solver.average_policy()


def rollout_policy(
    avg_policy: Any,
    deck_a: str,
    deck_b: str,
    game_seed: int,
    policy_seed: int,
) -> tuple[float, float]:
    game = build_open_spiel_game(seed=game_seed, deck_a=deck_a, deck_b=deck_b)
    state = game.new_initial_state()
    rng = random.Random(policy_seed)

    while not state.is_terminal():
        current = state.current_player()
        action_probs = avg_policy.action_probabilities(state, current)
        chosen = _sample_action(action_probs, rng)
        state.apply_action(chosen)

    returns = state.returns()
    return float(returns[0]), float(returns[1])


def evaluate_pairing(
    deck_a: str,
    deck_b: str,
    iterations: int,
    algorithm: str,
    eval_games: int,
    base_seed: int,
    eval_every: int,
    verbose: bool = True,
) -> PairingStats:
    wins_a = 0
    wins_b = 0
    draws = 0

    eval_iter: Any = range(eval_games)
    eval_progress = None
    if verbose and tqdm is not None:
        eval_progress = tqdm(eval_iter, total=eval_games, desc=f"pair {deck_a} vs {deck_b}", leave=False)
        eval_iter = eval_progress

    for offset in eval_iter:
        game_seed = base_seed + offset
        avg_policy = train_average_policy(
            deck_a=deck_a,
            deck_b=deck_b,
            iterations=iterations,
            algorithm=algorithm,
            seed=game_seed,
            eval_every=eval_every,
            verbose=False,
        )
        result_a, result_b = rollout_policy(
            avg_policy=avg_policy,
            deck_a=deck_a,
            deck_b=deck_b,
            game_seed=game_seed,
            policy_seed=game_seed + 10_000,
        )
        if result_a > result_b:
            wins_a += 1
        elif result_b > result_a:
            wins_b += 1
        else:
            draws += 1
        if eval_progress is not None:
            eval_progress.set_postfix_str(f"W/L/D={wins_a}/{wins_b}/{draws}")

    if verbose:
        print(
            f"pairing={deck_a} vs {deck_b}: wins_a={wins_a}, wins_b={wins_b}, draws={draws}, games={eval_games}"
        )

    return PairingStats(
        deck_a=deck_a,
        deck_b=deck_b,
        wins_a=wins_a,
        wins_b=wins_b,
        draws=draws,
        games=eval_games,
    )