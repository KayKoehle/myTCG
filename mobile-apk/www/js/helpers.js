export function laneLabel(locationId) {
    const laneId = Number(locationId);
    if (laneId === 0) return 'left lane';
    if (laneId === 1) return 'middle lane';
    if (laneId === 2) return 'right lane';
    return `lane ${laneId + 1}`;
}

function looksLikeCardId(value) {
    return typeof value === 'string' && /^[A-Za-z0-9_-]{8,}$/.test(value);
}

export function cardDisplayName(cardId, cardNameById) {
    if (cardNameById && cardNameById.has(cardId)) return cardNameById.get(cardId);
    return looksLikeCardId(cardId) ? 'Unknown card' : String(cardId || 'Unknown card');
}

export function actionLabel(action, cardNameById = new Map()) {
    if (action.kind === 'play_card') {
        return `Play ${cardDisplayName(action.card_id, cardNameById)} -> ${laneLabel(action.location_id)}`;
    }
    if (action.kind === 'choose_option') {
        if (action.option_id === 'KEEP') return 'Mulligan: KEEP';
        return `Choose: ${describeChoiceOption(action.option_id, cardNameById)}`;
    }
    if (action.kind === 'draw_card') return 'Draw card';
    return action.kind;
}

export function ordinalLabel(value) {
    const n = Number(value);
    const mod100 = n % 100;
    if (mod100 >= 11 && mod100 <= 13) return `${n}th`;
    const mod10 = n % 10;
    if (mod10 === 1) return `${n}st`;
    if (mod10 === 2) return `${n}nd`;
    if (mod10 === 3) return `${n}rd`;
    return `${n}th`;
}

export function gameOverStatusText(snapshot, humanPlayerId) {
    const history = Array.isArray(snapshot.action_history) ? snapshot.action_history : [];
    for (let i = history.length - 1; i >= 0; i -= 1) {
        const entry = String(history[i] || '');
        if (!entry.startsWith('game_result:')) continue;
        const winner = entry.split(':')[1] || '';
        if (winner === 'DRAW') return 'Game Over | Draw game';
        return winner === humanPlayerId
            ? 'Game Over | You won the game'
            : 'Game Over | Opponent won the game';
    }
    return 'Game Over | Start a new game';
}

export function cardPngUrl(cardName) {
    return `/assets/card_png/${encodeURIComponent(cardName)}.png`;
}

export function cardArtTag(cardName, cssClass) {
    const safeName = cardName || 'card';
    return `<img class="${cssClass}" src="${cardPngUrl(safeName)}" alt="${safeName}" loading="lazy" onerror="this.style.display='none';">`;
}

export function effectLabel(card) {
    const effect = (card && typeof card.effect === 'string') ? card.effect.trim() : '';
    return effect || 'No effect text';
}

export function typeLabel(card) {
    const type = (card && typeof card.type === 'string') ? card.type.trim() : '';
    if (!type) return '';
    const subtype = (card && typeof card.subtype === 'string') ? card.subtype.trim() : '';
    return subtype ? `${type} — ${subtype}` : type;
}

export function displayCardTitle(cardName) {
    const rawName = String(cardName || '').trim();
    if (!rawName) return '';
    if (rawName.length <= 18) return rawName;

    for (const separator of ['. ', ': ', ' - ', ', ']) {
        const index = rawName.indexOf(separator);
        if (index > 0) {
            const shortName = rawName.slice(0, index).trim();
            if (shortName.length >= 4) return shortName;
        }
    }

    return rawName;
}

export function handTitleScale(cardName) {
    const title = displayCardTitle(cardName);
    const titleLength = title.length;
    if (titleLength <= 14) return '1';

    const scale = Math.max(0.56, 13 / titleLength);
    return scale.toFixed(2);
}

export function escapeHtml(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

export function buildCardNameMap(snapshot) {
    const cardNameById = new Map();

    const known = snapshot && typeof snapshot.card_name_by_id === 'object' ? snapshot.card_name_by_id : {};
    for (const [cardId, cardName] of Object.entries(known)) {
        if (cardId && cardName) cardNameById.set(cardId, cardName);
    }

    const rememberCards = (cards) => {
        if (!Array.isArray(cards)) return;
        for (const card of cards) {
            if (!card || !card.id) continue;
            if (card.name) cardNameById.set(card.id, card.name);
        }
    };

    rememberCards(snapshot.hand || []);

    for (const lane of (snapshot.locations || [])) {
        if (!lane || !lane.stacks) continue;
        for (const stackCards of Object.values(lane.stacks)) {
            rememberCards(stackCards);
        }
    }

    for (const cards of Object.values(snapshot.underworld || {})) {
        rememberCards(cards);
    }

    return cardNameById;
}

export function describeChoiceOption(optionId, cardNameById, viewerSideIdx = null) {
    if (optionId === 'PASS') return 'Pass';
    if (optionId === 'KEEP') return 'Keep current hand';
    if (optionId === 'BOTTOM') return 'Move top card to bottom';
    if (optionId === 'NONE') return 'None';

    const directName = cardNameById.get(optionId);
    if (directName) return directName;

    if (typeof optionId === 'string') {
        const parts = optionId.split('|');
        const zone = parts[0];
        if (parts.length === 2 && (zone === 'hand' || zone === 'deck' || zone === 'underworld')) {
            const zoneName = zone.charAt(0).toUpperCase() + zone.slice(1);
            return `${zoneName}: ${cardDisplayName(parts[1], cardNameById)}`;
        }

        if (parts.length === 2) {
            const maybeCardId = parts[0];
            const maybeLane = Number(parts[1]);
            if (Number.isFinite(maybeLane)) {
                return `${cardDisplayName(maybeCardId, cardNameById)} -> ${laneLabel(maybeLane)}`;
            }

            const allNamed = parts.every((part) => cardNameById.has(part) || looksLikeCardId(part));
            if (allNamed) {
                return parts.map((part) => cardDisplayName(part, cardNameById)).join(', ');
            }
        }

        if (parts.length === 3) {
            const maybeLane = Number(parts[1]);
            const maybeSide = Number(parts[2]);
            if (Number.isFinite(maybeLane) && Number.isFinite(maybeSide)) {
                const sideSuffix = viewerSideIdx === null
                    ? ''
                    : (maybeSide === Number(viewerSideIdx) ? ' (your side)' : ' (opponent side)');
                return `${cardDisplayName(parts[0], cardNameById)} -> ${laneLabel(maybeLane)}${sideSuffix}`;
            }
        }
    }
    return looksLikeCardId(optionId) ? 'Unknown card' : optionId;
}

export function fillSelectFromOptions(selectEl, options, preferredValue) {
    const normalized = Array.from(new Set((options || []).filter(Boolean)));
    if (!normalized.length) return;
    selectEl.innerHTML = normalized.map((opt) => `<option value="${opt}">${opt}</option>`).join('');
    const nextValue = normalized.includes(preferredValue) ? preferredValue : normalized[0];
    selectEl.value = nextValue;
}

export function findCardById(snapshot, cardId) {
    if (!snapshot || !cardId) return null;

    const scan = (cards) => (Array.isArray(cards) ? cards.find((c) => c && c.id === cardId) : null);

    const inHand = scan(snapshot.hand);
    if (inHand) return inHand;

    for (const lane of (snapshot.locations || [])) {
        if (!lane || !lane.stacks) continue;
        for (const stackCards of Object.values(lane.stacks)) {
            const found = scan(stackCards);
            if (found) return found;
        }
    }

    for (const cards of Object.values(snapshot.underworld || {})) {
        const found = scan(cards);
        if (found) return found;
    }

    return scan(snapshot.opponent_hand);
}

export function stackPower(cards) {
    if (!cards || cards.length === 0) return 0;
    return cards.reduce((sum, card) => sum + (Number(card.power) || 0), 0);
}

export function humanLegalActions(snapshot, playerId) {
    return (snapshot.legal_actions || []).filter((a) => Number(a.player_id) === Number(playerId));
}
