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

export function cardPngUrl(cardName) {
    return `/assets/card_png/${encodeURIComponent(cardName)}.png`;
}

export function cardArtTag(cardName, cssClass, { eager = false } = {}) {
    const safeName = cardName || 'card';
    // draggable="false" keeps the browser's native image drag from hijacking
    // the pointer-based card drag on desktop. In-game art loads eagerly:
    // lazy-loaded images flash (effect text shows through) on every re-render.
    return `<img class="${cssClass}" src="${cardPngUrl(safeName)}" alt="${safeName}" draggable="false" loading="${eager ? 'eager' : 'lazy'}" onerror="this.style.display='none';">`;
}

// Warm the browser cache for card art so re-renders (mulligan, opponent
// plays, lane updates) swap images in without a visible flicker.
const preloadedArtNames = new Set();

export function preloadCardArt(cardNames) {
    for (const name of cardNames || []) {
        if (!name || preloadedArtNames.has(name)) continue;
        preloadedArtNames.add(name);
        const img = new Image();
        img.src = cardPngUrl(name);
    }
}

export function effectLabel(card) {
    const effect = (card && typeof card.effect === 'string') ? card.effect.trim() : '';
    return effect || 'No effect text';
}

// The card's flavour/lore text. Unlike the effect it is optional — many cards
// have none — so this returns '' when absent and callers hide their block.
export function anecdoteText(card) {
    return (card && typeof card.anecdote === 'string') ? card.anecdote.trim() : '';
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

    // A lone location index (e.g. the Ark's "choose a location" choice, or
    // the revive-destination follow-up choice) reads far better as a lane name.
    if (typeof optionId === 'string' && /^\d+$/.test(optionId)) {
        return `Move to ${laneLabel(Number(optionId))}`;
    }

    if (typeof optionId === 'string') {
        const parts = optionId.split('|');
        const zone = parts[0];
        if (parts.length === 2 && (zone === 'hand' || zone === 'deck' || zone === 'underworld')) {
            const zoneName = zone.charAt(0).toUpperCase() + zone.slice(1);
            return `${zoneName}: ${cardDisplayName(parts[1], cardNameById)}`;
        }

        if (parts.length === 2 && zone === 'BOTTOM') {
            return `Bury: ${cardDisplayName(parts[1], cardNameById)}`;
        }

        // Dolon in multiplayer: first pick whose deck to scout ("OPP|<side>").
        if (parts.length === 2 && zone === 'OPP') {
            return `Scout rival P${Number(parts[1]) + 1}'s deck`;
        }

        if (parts.length === 2) {
            const maybeCardId = parts[0];
            const maybeLane = Number(parts[1]);
            // "location|side" pairs (e.g. Enkidu joining Gilgamesh) carry no card id.
            if (Number.isFinite(Number(parts[0])) && Number.isFinite(maybeLane)) {
                return `Move to ${laneLabel(Number(parts[0]))}`;
            }
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

        // A combination of card ids this function can't otherwise decode
        // (e.g. a multi-pick combo) must never leak raw card ids to the UI.
        if (parts.length > 1 && parts.every((part) => cardNameById.has(part) || looksLikeCardId(part))) {
            return parts.map((part) => cardDisplayName(part, cardNameById)).join(', ');
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

    const inOppHand = scan(snapshot.opponent_hand);
    if (inOppHand) return inOppHand;

    // Cards revealed by a deck peek (Calchas, Dolon) never sit in an exposed
    // zone; fall back to the full-detail map of every card in both decks.
    if (snapshot.known_cards && snapshot.known_cards[cardId]) return snapshot.known_cards[cardId];

    return null;
}

// A stat (cost/power) modified by an effect (Humbaba, Diomedes, ...) colors
// green when it's better for the card's owner (cheaper cost, higher power)
// and red when it's worse. Returns '' when unaffected or unknown.
export function statChangeClass(current, base, higherIsBetter) {
    if (current === null || current === undefined || base === null || base === undefined) return '';
    if (current === base) return '';
    const better = higherIsBetter ? current > base : current < base;
    return better ? 'stat-better' : 'stat-worse';
}

export function stackPower(cards) {
    if (!cards || cards.length === 0) return 0;
    return cards.reduce((sum, card) => sum + (Number(card.power) || 0), 0);
}

export function humanLegalActions(snapshot, playerId) {
    return (snapshot.legal_actions || []).filter((a) => Number(a.player_id) === Number(playerId));
}

// A transient toast at the top of the screen, above every screen and modal.
// tone 'gold' is for rewards (quests, weekend bonuses), 'info' for the rest.
export function showToast(message, tone = 'info') {
    const toast = document.createElement('div');
    toast.className = `app-toast ${tone === 'gold' ? 'app-toast-gold' : ''}`;
    toast.textContent = String(message);
    document.body.appendChild(toast);
    const remove = () => toast.remove();
    if (!window.Element.prototype.animate) {
        setTimeout(remove, 2800);
        return;
    }
    const anim = toast.animate(
        [
            { transform: 'translate(-50%, -16px)', opacity: 0 },
            { transform: 'translate(-50%, 0)', opacity: 1, offset: 0.12 },
            { transform: 'translate(-50%, 0)', opacity: 1, offset: 0.85 },
            { transform: 'translate(-50%, -10px)', opacity: 0 },
        ],
        { duration: 3200, easing: 'ease-out' }
    );
    anim.addEventListener('finish', remove);
    anim.addEventListener('cancel', remove);
}
