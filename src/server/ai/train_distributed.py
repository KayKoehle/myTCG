from __future__ import annotations

import argparse

from ..engine.training import rollout_neural_policy, train_neural_policy_distributed


DEFAULT_GENERALIST_DECKS = [
    "epic_of_gilgamesh",
    "inannas_descent",
    "the_flood",
    "siege_of_troy",
]


def run(args: argparse.Namespace) -> int:
    deck_pool = tuple(deck.strip() for deck in args.decks.split(",") if deck.strip())
    if len(deck_pool) < 2:
        raise ValueError("--decks must contain at least two comma-separated deck names")

    try:
        training = train_neural_policy_distributed(
            deck_a=args.deck_a,
            deck_b=args.deck_b,
            episodes=args.episodes,
            seed=args.seed,
            lr=args.lr,
            hidden_dim=args.hidden_dim,
            feature_dim=args.feature_dim,
            gamma=args.gamma,
            num_actors=args.num_actors,
            episodes_per_update=args.episodes_per_update,
            ppo_epochs=args.ppo_epochs,
            clip_eps=args.clip_eps,
            value_coef=args.value_coef,
            entropy_coef=args.entropy_coef,
            league_pool_size=args.league_pool_size,
            league_add_every_updates=args.league_add_every_updates,
            elo_eval_every_updates=args.elo_eval_every_updates,
            elo_eval_games=args.elo_eval_games,
            checkpoint_path=args.checkpoint_path,
            checkpoint_every_updates=args.checkpoint_every_updates,
            elo_csv_path=args.elo_csv,
            resume_from=args.resume_from,
            deck_pool=deck_pool,
            pipeline_mode=args.pipeline_mode,
            league_sample_prob=args.league_sample_prob,
            device=args.device,
            verbose=True,
        )
    except ImportError as exc:
        print(str(exc))
        return 2

    eval_deck_a, eval_deck_b = deck_pool[0], deck_pool[1]
    result = rollout_neural_policy(
        neural_policy=training.policy,
        deck_a=eval_deck_a,
        deck_b=eval_deck_b,
        game_seed=args.seed,
    )
    print(f"device={training.policy.device}")
    print(f"trained_decks={','.join(deck_pool)}")
    if training.elo_history:
        last = training.elo_history[-1]
        print(f"elo_final={last.rating:.3f}")
        print(f"elo_points={len(training.elo_history)}")
    print(f"terminal_returns={list(result)}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Distributed actor-learner PPO training with league snapshots")
    parser.add_argument("--episodes", type=int, default=1000, help="Total self-play episodes")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic game seed")
    parser.add_argument("--lr", type=float, default=1e-3, help="Adam learning rate")
    parser.add_argument("--hidden-dim", type=int, default=512, help="Hidden width for actor-critic")
    parser.add_argument("--feature-dim", type=int, default=4096, help="Observation feature dimension")
    parser.add_argument("--gamma", type=float, default=1.0, help="Reward discount factor")
    parser.add_argument("--num-actors", type=int, default=8, help="Number of rollout actor processes")
    parser.add_argument("--episodes-per-update", type=int, default=32, help="Episodes gathered per learner update")
    parser.add_argument("--ppo-epochs", type=int, default=3, help="PPO optimization epochs")
    parser.add_argument("--clip-eps", type=float, default=0.2, help="PPO clip epsilon")
    parser.add_argument("--value-coef", type=float, default=0.5, help="Value loss coefficient")
    parser.add_argument("--entropy-coef", type=float, default=0.01, help="Entropy coefficient")
    parser.add_argument("--league-pool-size", type=int, default=16, help="Maximum opponent snapshot pool size")
    parser.add_argument("--league-add-every-updates", type=int, default=5, help="How often to add current snapshot to league pool")
    parser.add_argument("--elo-eval-every-updates", type=int, default=10, help="How often to evaluate Elo")
    parser.add_argument("--elo-eval-games", type=int, default=8, help="Games per Elo evaluation")
    parser.add_argument("--elo-csv", type=str, default="stats/ai_training_elo_distributed.csv", help="Elo history CSV output")
    parser.add_argument("--checkpoint-path", type=str, default="stats/checkpoints/ai_nn_distributed_latest.pt", help="Checkpoint file path")
    parser.add_argument("--checkpoint-every-updates", type=int, default=10, help="Checkpoint interval")
    parser.add_argument("--resume-from", type=str, default=None, help="Optional checkpoint file to resume from")
    parser.add_argument(
        "--pipeline-mode",
        type=str,
        default="shared_memory",
        choices=["shared_memory", "queue", "rpc"],
        help="Distributed transport mode. queue/rpc currently alias to shared_memory.",
    )
    parser.add_argument(
        "--league-sample-prob",
        type=float,
        default=0.5,
        help="Probability that actors sample opponent from league snapshots instead of current policy",
    )
    parser.add_argument("--device", type=str, default="auto", choices=["auto", "cuda", "cpu"], help="Learner device")
    parser.add_argument(
        "--decks",
        type=str,
        default=",".join(DEFAULT_GENERALIST_DECKS),
        help="Comma-separated deck pool used for generalist self-play training",
    )
    parser.add_argument("--deck-a", type=str, default="epic_of_gilgamesh", help="Compatibility fallback deck A (unused when --decks is provided)")
    parser.add_argument("--deck-b", type=str, default="siege_of_troy", help="Compatibility fallback deck B (unused when --decks is provided)")
    return parser.parse_args()


def main() -> int:
    return run(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
