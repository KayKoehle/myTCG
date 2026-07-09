export function createAppState() {
    return {
        matchId: `snap-match-${Math.floor(Math.random() * 1_000_000)}`,
        humanPlayerId: 1,
        aiPlayerId: 2,
        seed: Math.floor(Math.random() * 1_000_000_000),
        defaultDeckA: 'epic_of_gilgamesh',
        defaultDeckB: 'siege_of_troy',
        // Set by the main menu's Play flow; they win over the settings-sheet
        // deck dropdowns (which clear them when used).
        deckAName: null,
        deckBName: null,
        deckACards: null,
        snapshot: null,
        selectedCardId: null,
        legalPlaySet: new Set(),
        legalMoveChoiceSet: new Set(),
        movableChoiceCardSet: new Set(),
        playableCardSet: new Set(),
        abilityReadyCardSet: new Set(),
        cardNameById: new Map(),
        mulliganSelected: new Set(),
        autoRunning: false,
        actionPending: false,
        dragEndedAt: 0,
        opponentTurnActive: false,
        opponentTurnStartedAt: 0,
        // History-driven animations (round crowns, opponent mulligans) fire on
        // entries added after these markers; reset per match.
        animMatchId: null,
        historySeen: 0,
    };
}

export function buildConfig(ui, app) {
    return {
        match_id: app.matchId,
        player_id: app.humanPlayerId,
        ai_player_id: app.aiPlayerId,
        viewer_player_id: app.humanPlayerId,
        seed: app.seed,
        deck_a: app.deckAName || ui.deckA.value.trim() || app.defaultDeckA,
        deck_b: app.deckBName || ui.deckB.value.trim() || app.defaultDeckB,
        deck_a_cards: app.deckACards || null,
        checkpoint_path: ui.checkpointPath.value.trim(),
        device: 'auto',
    };
}
