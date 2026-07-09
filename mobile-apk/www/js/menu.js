import { postJson } from './api.js';
import { cardArtTag, cardPngUrl, escapeHtml } from './helpers.js';
import {
    BOARDS,
    CARD_BACKS,
    DECK_IDS,
    DECK_META,
    EMOTES,
    applyCosmetics,
    buyItem,
    deckCardIds,
    deckDisplayName,
    deckIsEdited,
    deckMatchConfig,
    equipItem,
    equippedItem,
    getCrowns,
    getSelectedDeckId,
    ownsItem,
    renameDeck,
    resetDeck,
    selectDeck,
    setDeckCards,
} from './profile.js';

// Menu / Decks / Shop screens. The game screen itself stays owned by the
// game controller; this controller only decides which screen is visible and
// starts matches (player's selected deck vs. a random stock AI deck).
export function createMenuController(ui, game) {
    // Stock deck lists + full card details, fetched once from /api/collection.
    let stockCardsByDeck = null; // Map deckId -> [card, ...] (deck order)
    let collectionError = null;
    let editorDeckId = null; // deck being edited on the Decks screen
    let editorSelectedCardId = null; // deck card marked for a swap
    let shopTab = 'cardBack';

    const SHOP_TABS = [
        { kind: 'cardBack', label: 'Card Backs', items: CARD_BACKS },
        { kind: 'emote', label: 'Emotes', items: EMOTES },
        { kind: 'board', label: 'Boards', items: BOARDS },
    ];

    async function ensureCollection() {
        if (stockCardsByDeck) return stockCardsByDeck;
        try {
            const data = await postJson('/api/collection', {});
            stockCardsByDeck = new Map();
            for (const deck of (data.decks || [])) {
                stockCardsByDeck.set(deck.deck_id, deck.cards || []);
            }
            collectionError = null;
        } catch (error) {
            collectionError = String(error);
        }
        return stockCardsByDeck;
    }

    function stockIds(deckId) {
        const cards = (stockCardsByDeck && stockCardsByDeck.get(deckId)) || [];
        return cards.map((c) => c.id);
    }

    function cardById(cardId) {
        if (!stockCardsByDeck) return null;
        for (const cards of stockCardsByDeck.values()) {
            const found = cards.find((c) => c.id === cardId);
            if (found) return found;
        }
        return null;
    }

    function allCollectionCards() {
        if (!stockCardsByDeck) return [];
        const all = [];
        for (const deckId of DECK_IDS) {
            all.push(...(stockCardsByDeck.get(deckId) || []));
        }
        return all;
    }

    // --- Screen switching -------------------------------------------------

    function showScreen(name) {
        const screens = {
            menu: ui.menuScreen,
            decks: ui.decksScreen,
            shop: ui.shopScreen,
            game: ui.gameScreen,
        };
        for (const el of Object.values(screens)) {
            if (el) el.classList.remove('active');
        }
        if (screens[name]) screens[name].classList.add('active');
    }

    function updateCrowns() {
        const crowns = String(getCrowns());
        if (ui.menuCrowns) ui.menuCrowns.textContent = crowns;
        if (ui.shopCrowns) ui.shopCrowns.textContent = crowns;
    }

    // --- Main menu -----------------------------------------------------------

    function renderMenu() {
        updateCrowns();
        const deckId = getSelectedDeckId();
        const mainCard = DECK_META[deckId].mainCard;
        ui.menuDeckArt.innerHTML = `<img src="${cardPngUrl(mainCard)}" alt="${escapeHtml(mainCard)}" draggable="false" loading="lazy" onerror="this.style.display='none';">`;
        ui.menuDeckName.textContent = deckDisplayName(deckId);
    }

    function openMenu() {
        renderMenu();
        showScreen('menu');
    }

    async function play() {
        await ensureCollection();
        const deckId = getSelectedDeckId();
        const deckA = deckMatchConfig(deckId, stockIds(deckId));
        // The AI draws a random stock deck. Mirror matches are fine: the
        // engine aliases shared card ids for side B.
        const aiDeckId = DECK_IDS[Math.floor(Math.random() * DECK_IDS.length)];
        showScreen('game');
        await game.startGame({
            deckAName: deckA.name,
            deckACards: deckA.cards,
            deckBName: aiDeckId,
        });
    }

    // --- Decks screen ----------------------------------------------------------

    function miniCardHtml(card, { inDeck = false, selected = false } = {}) {
        if (!card) return '';
        return `
            <div class="mini-card ${inDeck ? 'in-deck' : ''} ${selected ? 'selected' : ''}" data-card-id="${escapeHtml(card.id)}">
                <div class="mini-head">
                    <span class="stat-badge cost">${card.cost ?? '?'}</span>
                    <span class="stat-badge power">${card.power !== null && card.power !== undefined ? card.power : '?'}</span>
                </div>
                <div class="mini-media">${cardArtTag(card.name, 'mini-art')}</div>
                <div class="mini-name">${escapeHtml(card.name || '')}</div>
                ${inDeck ? '<div class="mini-flag">In deck</div>' : ''}
            </div>
        `;
    }

    function renderDeckRow() {
        const selectedId = getSelectedDeckId();
        ui.decksTitle.textContent = 'Decks';
        ui.decksTop.innerHTML = `
            <div class="deck-row">
                ${DECK_IDS.map((deckId) => {
                    const mainCard = DECK_META[deckId].mainCard;
                    return `
                        <button class="deck-tile ${deckId === selectedId ? 'selected' : ''}" data-deck-id="${deckId}">
                            <div class="deck-tile-art">
                                <img src="${cardPngUrl(mainCard)}" alt="${escapeHtml(mainCard)}" draggable="false" loading="lazy" onerror="this.style.display='none';">
                            </div>
                            <div class="deck-tile-name">${escapeHtml(deckDisplayName(deckId))}</div>
                            <div class="deck-tile-tags">
                                ${deckId === selectedId ? '<span class="deck-tag deck-tag-selected">Selected</span>' : ''}
                                ${deckIsEdited(deckId) ? '<span class="deck-tag">Edited</span>' : ''}
                            </div>
                        </button>
                    `;
                }).join('')}
            </div>
        `;
        ui.decksTop.querySelectorAll('.deck-tile[data-deck-id]').forEach((tile) => {
            tile.addEventListener('click', () => {
                const deckId = tile.dataset.deckId;
                selectDeck(deckId);
                editorDeckId = deckId;
                editorSelectedCardId = null;
                renderDecks();
            });
        });
    }

    function currentEditorIds() {
        return deckCardIds(editorDeckId, stockIds(editorDeckId));
    }

    function renderDeckEditor() {
        const deckId = editorDeckId;
        const ids = currentEditorIds();
        ui.decksTitle.textContent = 'Edit deck';
        ui.decksTop.innerHTML = `
            <div class="deck-editor">
                <div class="deck-editor-head">
                    <input class="deck-name-input" id="deckNameInput" maxlength="30"
                        value="${escapeHtml(deckDisplayName(deckId))}" aria-label="Deck name">
                    <button class="btn deck-reset-btn" id="deckResetBtn" title="Restore the stock deck list and name">Reset</button>
                </div>
                <div class="deck-editor-hint">${editorSelectedCardId
                    ? 'Now tap a collection card below to swap it in.'
                    : 'Tap a deck card, then a collection card below to swap.'}</div>
                <div class="deck-grid">
                    ${ids.map((cardId) => miniCardHtml(cardById(cardId), { selected: cardId === editorSelectedCardId })).join('')}
                </div>
            </div>
        `;

        const nameInput = ui.decksTop.querySelector('#deckNameInput');
        nameInput.addEventListener('change', () => {
            renameDeck(deckId, nameInput.value);
            nameInput.value = deckDisplayName(deckId);
            renderMenu();
        });
        ui.decksTop.querySelector('#deckResetBtn').addEventListener('click', () => {
            resetDeck(deckId);
            editorSelectedCardId = null;
            renderDecks();
            renderMenu();
        });
        ui.decksTop.querySelectorAll('.mini-card[data-card-id]').forEach((cardEl) => {
            cardEl.addEventListener('click', () => {
                const cardId = cardEl.dataset.cardId;
                editorSelectedCardId = editorSelectedCardId === cardId ? null : cardId;
                renderDecks();
            });
        });
    }

    function renderCollection() {
        if (collectionError) {
            ui.collectionGrid.innerHTML = `<div class="tiny">Collection unavailable: ${escapeHtml(collectionError)}</div>`;
            return;
        }
        const cards = allCollectionCards();
        if (!cards.length) {
            ui.collectionGrid.innerHTML = '<div class="tiny">Loading collection…</div>';
            return;
        }
        const deckIds = editorDeckId ? new Set(currentEditorIds()) : new Set();
        ui.collectionLabel.textContent = editorDeckId
            ? 'Your collection — tap a card to swap it into the deck'
            : 'Your collection';
        ui.collectionGrid.innerHTML = cards
            .map((card) => miniCardHtml(card, { inDeck: deckIds.has(card.id) }))
            .join('');

        ui.collectionGrid.querySelectorAll('.mini-card[data-card-id]').forEach((cardEl) => {
            cardEl.addEventListener('click', () => {
                if (!editorDeckId) return;
                const cardId = cardEl.dataset.cardId;
                if (deckIds.has(cardId)) {
                    // Its copy in the deck grid is the same card: mark it for a swap.
                    editorSelectedCardId = editorSelectedCardId === cardId ? null : cardId;
                    renderDecks();
                    return;
                }
                if (!editorSelectedCardId) return;
                const ids = currentEditorIds().map((id) => (id === editorSelectedCardId ? cardId : id));
                setDeckCards(editorDeckId, ids, stockIds(editorDeckId));
                editorSelectedCardId = null;
                renderDecks();
            });
        });
    }

    function renderDecks() {
        if (editorDeckId) {
            renderDeckEditor();
        } else {
            renderDeckRow();
        }
        renderCollection();
    }

    async function openDecks() {
        editorDeckId = null;
        editorSelectedCardId = null;
        showScreen('decks');
        renderDecks();
        await ensureCollection();
        renderDecks();
    }

    // --- Shop screen ---------------------------------------------------------

    function shopPreviewHtml(kind, item) {
        if (kind === 'cardBack') return `<div class="cardback-preview" data-cardback="${item.id}"></div>`;
        if (kind === 'board') return `<div class="board-preview" data-board="${item.id}"></div>`;
        return `<div class="emote-preview">${escapeHtml(item.text)}</div>`;
    }

    function shopButtonHtml(kind, item) {
        const owned = ownsItem(kind, item.id);
        if (!owned) {
            const affordable = getCrowns() >= item.cost;
            return `<button class="btn shop-item-btn" data-shop-action="buy" ${affordable ? '' : 'disabled'}>
                Buy · ${item.cost} <span class="crown-icon"></span></button>`;
        }
        if (kind === 'emote') {
            return '<button class="btn shop-item-btn owned" disabled>Owned</button>';
        }
        const equipped = equippedItem(kind) === item.id;
        return equipped
            ? '<button class="btn shop-item-btn equipped" disabled>Equipped</button>'
            : '<button class="btn shop-item-btn" data-shop-action="equip">Equip</button>';
    }

    function renderShop() {
        updateCrowns();
        ui.shopTabs.innerHTML = SHOP_TABS.map((tab) => `
            <button class="shop-tab ${tab.kind === shopTab ? 'active' : ''}" data-shop-tab="${tab.kind}">${tab.label}</button>
        `).join('');
        ui.shopTabs.querySelectorAll('[data-shop-tab]').forEach((btn) => {
            btn.addEventListener('click', () => {
                shopTab = btn.dataset.shopTab;
                renderShop();
            });
        });

        const tab = SHOP_TABS.find((t) => t.kind === shopTab) || SHOP_TABS[0];
        ui.shopItems.innerHTML = tab.items.map((item) => `
            <div class="shop-item" data-item-id="${item.id}">
                ${shopPreviewHtml(tab.kind, item)}
                ${tab.kind === 'emote' ? '' : `<div class="shop-item-name">${escapeHtml(item.name)}</div>`}
                ${shopButtonHtml(tab.kind, item)}
            </div>
        `).join('');

        ui.shopItems.querySelectorAll('[data-shop-action]').forEach((btn) => {
            btn.addEventListener('click', () => {
                const itemEl = btn.closest('.shop-item');
                const item = tab.items.find((i) => i.id === itemEl.dataset.itemId);
                if (!item) return;
                if (btn.dataset.shopAction === 'buy') {
                    if (buyItem(tab.kind, item.id, item.cost) && tab.kind !== 'emote') {
                        equipItem(tab.kind, item.id); // wear new cosmetics right away
                    }
                } else {
                    equipItem(tab.kind, item.id);
                }
                renderShop();
            });
        });
    }

    function openShop() {
        showScreen('shop');
        renderShop();
    }

    // --- Wiring ------------------------------------------------------------------

    function init() {
        applyCosmetics();
        ui.btnMenuPlay.addEventListener('click', () => {
            play();
        });
        ui.btnMenuDecks.addEventListener('click', () => {
            openDecks();
        });
        ui.btnMenuShop.addEventListener('click', () => {
            openShop();
        });
        ui.btnDecksBack.addEventListener('click', () => {
            if (editorDeckId) {
                editorDeckId = null;
                editorSelectedCardId = null;
                renderDecks();
            } else {
                openMenu();
            }
        });
        ui.btnShopBack.addEventListener('click', () => {
            openMenu();
        });
        renderMenu();
        ensureCollection(); // warm the collection cache in the background
    }

    return { init, openMenu, showScreen };
}
