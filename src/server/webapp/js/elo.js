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
    return Math.round(delta);
}
