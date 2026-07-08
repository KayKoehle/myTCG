export function createAppState() {
    return {
        matchId: `snap-match-${Math.floor(Math.random() * 1_000_000)}`,
        humanPlayerId: 1,
        aiPlayerId: 2,
        seed: Math.floor(Math.random() * 1_000_000_000),
        defaultDeckA: 'epic_of_gilgamesh',
        defaultDeckB: 'siege_of_troy',
        snapshot: null,
        selectedCardId: null,
        legalPlaySet: new Set(),
        legalMoveChoiceSet: new Set(),
        movableChoiceCardSet: new Set(),
        playableCardSet: new Set(),
        cardNameById: new Map(),
        mulliganSelected: new Set(),
        autoRunning: false,
        dragEndedAt: 0,
    };
}

export function buildConfig(ui, app) {
    return {
        match_id: app.matchId,
        player_id: app.humanPlayerId,
        ai_player_id: app.aiPlayerId,
        viewer_player_id: app.humanPlayerId,
        seed: app.seed,
        deck_a: ui.deckA.value.trim() || app.defaultDeckA,
        deck_b: ui.deckB.value.trim() || app.defaultDeckB,
        checkpoint_path: ui.checkpointPath.value.trim(),
        device: 'auto',
    };
}
