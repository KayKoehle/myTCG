// Elo ratings: the player carries ONE rating across all game modes (duel and
// every FFA size). AI opponents are rated players too — their rating is
// sampled near the player's before each match, and the server's Elo ladder
// (engine/ladder.py) makes them actually play at that strength.

// Playable rating range — keep in sync with TIER_ANCHORS in engine/ladder.py
// (random 575 ... minimax 1300, calibrated by arena cross-play).
export const AI_MIN_ELO = 575;
export const AI_MAX_ELO = 1300;

export const DEFAULT_ELO = 1000;
export const ELO_FLOOR = 100;
const K_FACTOR = 32;

// How far above/below the player a matchmade AI rival may land.
const MATCH_SPREAD = 120;

export function expectedScore(rating, opponentRating) {
    return 1 / (1 + Math.pow(10, (opponentRating - rating) / 400));
}

// The rating an AI rival enters the match with: close to the player (so
// games stay fair and rating gains meaningful), clamped to the range the
// ladder can actually play at.
export function sampleAiElo(playerElo) {
    const jitter = Math.round((Math.random() * 2 - 1) * MATCH_SPREAD);
    return Math.max(AI_MIN_ELO, Math.min(AI_MAX_ELO, Math.round(playerElo) + jitter));
}

// Final placements from victory points: highest VP places first. Returns
// {playerId: rank} with 1 the winner; equal VP shares the same rank (a tie).
// Surrender already works through VP (the engine zeroes the surrenderer and
// awards 4 to the best rival), so no special casing here.
export function placementsFromVp(victoryPoints) {
    const entries = Object.entries(victoryPoints || {})
        .map(([pid, vp]) => ({ pid: Number(pid), vp: Number(vp) || 0 }))
        .sort((a, b) => b.vp - a.vp);
    const ranks = {};
    entries.forEach((entry, i) => {
        ranks[entry.pid] = i > 0 && entries[i - 1].vp === entry.vp ? ranks[entries[i - 1].pid] : i + 1;
    });
    return ranks;
}

// Winning a free-for-all outright means beating every rival at once, so the
// gain grows with the field: +25% per rival beyond the first (3P ×1.25,
// 4P ×1.5, 5P ×1.75). Losses and duels are untouched.
const FFA_WIN_BONUS_PER_RIVAL = 0.25;

// Multiplayer (and 1v1) Elo update, the standard pairwise generalization:
// the game is scored as one Elo game against EACH rival — win 1, tie 0.5,
// loss 0 by final placement — and the K factor is split across the rivals so
// a 5-player FFA moves the rating about as much as one duel does.
// With one opponent this reduces exactly to classic Elo.
export function eloDelta(playerElo, rivals, playerRank, ranks) {
    if (!rivals.length) return 0;
    const kPerRival = K_FACTOR / rivals.length;
    let delta = 0;
    for (const rival of rivals) {
        const rivalRank = ranks[rival.playerId];
        const score = playerRank < rivalRank ? 1 : (playerRank === rivalRank ? 0.5 : 0);
        delta += kPerRival * (score - expectedScore(playerElo, rival.elo));
    }
    const wonOutright = playerRank === 1 && rivals.every((rival) => ranks[rival.playerId] > 1);
    if (wonOutright && rivals.length > 1 && delta > 0) {
        delta *= 1 + FFA_WIN_BONUS_PER_RIVAL * (rivals.length - 1);
    }
    return Math.round(delta);
}

// Consecutive rated wins heat the rating up: +10% Elo per win already on the
// streak, capped at +50%. `streak` counts the current win too, so the bonus
// starts on the second straight win.
const STREAK_BONUS_PER_WIN = 0.1;
const STREAK_BONUS_CAP = 0.5;

export function streakMultiplier(streak) {
    const priorWins = Math.max(0, (Number(streak) || 0) - 1);
    return 1 + Math.min(STREAK_BONUS_CAP, STREAK_BONUS_PER_WIN * priorWins);
}
