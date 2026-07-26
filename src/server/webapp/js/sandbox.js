// Sandbox mode: the playtester's tools *inside* a normal match.
//
// There is no sandbox screen and no sandbox board. A player switches sandbox
// mode on from the History sheet of a regular game against the AI, and from then
// on the ordinary game screen grows a handful of affordances:
//
//   * a sandbox card at the end of the hand — the toolbox (add / draw / discard,
//     who controls which seat, undo),
//   * every pile is tappable: your own and the opponent's hand, deck and
//     underworld open an editable list of the cards really in them,
//   * every location gets a 🧪 button (move, banish, return to hand, add a card),
//   * mana gems and crowns are clickable, one by one.
//
// All of it funnels into one sheet (`#sandboxMenuModal`) that renders a stack of
// plain buttons, plus the card picker (`#sandboxPickerModal`) for "add any card".
// Menus are declared as builder functions and re-run after every edit, so a list
// never shows a card that has already moved.
//
// The state itself lives where it always did: on the server, in the match. Edits
// post to /api/sandbox/mutate and come back as an ordinary snapshot (with the
// omniscient `snapshot.sandbox` block attached), which the controller renders
// exactly like any other.

import { postJson } from './api.js';
import { cardArtTag, escapeHtml, typeLabel } from './helpers.js';

const ZONE_LABELS = {
    hand: 'Hand',
    deck: 'Deck',
    underworld: 'Underworld',
    set_aside: 'Set aside',
};

const ZONE_ICONS = {
    hand: '✋',
    deck: '🂠',
    underworld: '🕯',
    set_aside: '◇',
};

export function createSandboxTools(ui, deps) {
    // Every printed card, fetched once for the "add a card" picker.
    let catalog = [];
    // Menu builders, innermost last — so a submenu can offer "‹ Back".
    let menuStack = [];
    // The items of the menu currently on screen, indexed by the buttons' data-i.
    let menuItems = [];
    let picker = null; // { title, query, onPick }

    const snapshot = () => deps.getSnapshot();
    const sandboxState = () => (snapshot() || {}).sandbox || null;
    // Sandbox mode is on when the server says so *and* the player has not
    // tucked the tools away again (see deps.toolsVisible / hideTools).
    const active = () => Boolean(sandboxState()) && deps.toolsVisible();
    const youId = () => Number(deps.getConfig().player_id);

    // --- state lookups -------------------------------------------------------

    function seats() {
        const state = sandboxState();
        return state ? state.seats : [];
    }

    function seatOf(playerId) {
        return seats().find((seat) => Number(seat.player_id) === Number(playerId)) || null;
    }

    function playerIds() {
        return seats().map((seat) => Number(seat.player_id));
    }

    function label(playerId) {
        const name = deps.seatLabel(playerId);
        return Number(playerId) === youId() ? `${name} (you)` : name;
    }

    function laneName(locationId) {
        return deps.laneLabel(Number(locationId));
    }

    // Where a card is right now: { card, zone, playerId, locationId }.
    function findCard(cardId) {
        const state = sandboxState();
        if (!state) return null;
        for (const seat of state.seats) {
            for (const zone of ['hand', 'deck', 'underworld', 'set_aside']) {
                const found = (seat[zone] || []).find((card) => card.id === cardId);
                if (found) return { card: found, zone, playerId: Number(seat.player_id), locationId: null };
            }
        }
        for (const loc of (state.locations || [])) {
            for (const [pid, cards] of Object.entries(loc.stacks || {})) {
                const found = (cards || []).find((card) => card.id === cardId);
                if (found) {
                    return { card: found, zone: 'location', playerId: Number(pid), locationId: Number(loc.location_id) };
                }
            }
        }
        return null;
    }

    function locationOf(locationId) {
        const state = sandboxState();
        return (state && (state.locations || []).find((loc) => Number(loc.location_id) === Number(locationId))) || null;
    }

    // --- server calls --------------------------------------------------------

    async function ensureCatalog() {
        if (catalog.length) return;
        try {
            const data = await postJson('/api/sandbox/catalog', {});
            catalog = data.cards || [];
        } catch (error) {
            deps.flashStatus(`Could not load the card list: ${error}`);
        }
    }

    async function mutate(ops) {
        const done = await deps.mutate(Array.isArray(ops) ? ops : [ops]);
        // The board changed underneath the open menu; rebuild it from the new
        // state (or drop it if what it was about is gone).
        if (done && menuStack.length) renderMenu();
        return done;
    }

    // --- the menu sheet ------------------------------------------------------

    function openMenu(builder) {
        menuStack.push(builder);
        renderMenu();
        openSheet(ui.sandboxMenuModal);
    }

    function closeMenu() {
        menuStack = [];
        menuItems = [];
        closeSheet(ui.sandboxMenuModal);
    }

    function backMenu() {
        menuStack.pop();
        if (!menuStack.length) {
            closeMenu();
            return;
        }
        renderMenu();
    }

    // A menu is { title, hint, groups: [{ label, items }] }. An item is either
    // a plain button ({ label, icon, note, cls, run }) or a card row
    // ({ card, note, run }).
    function renderMenu() {
        const builder = menuStack[menuStack.length - 1];
        if (!builder) return;
        const menu = builder();
        if (!menu) {
            backMenu();
            return;
        }
        menuItems = [];
        ui.sandboxMenuTitle.textContent = menu.title || 'Sandbox';
        ui.sandboxMenuHint.textContent = menu.hint || '';
        ui.sandboxMenuHint.classList.toggle('hidden', !menu.hint);
        const groups = (menu.groups || []).filter((group) => group && (group.items || []).length);
        ui.sandboxMenuBody.innerHTML = groups.map((group) => `
            <div class="sbx-group">
                ${group.label ? `<div class="sbx-group-label">${escapeHtml(group.label)}</div>` : ''}
                <div class="sbx-items">${group.items.map(renderItem).join('')}</div>
            </div>`).join('');
        ui.btnSandboxMenuBack.classList.toggle('hidden', menuStack.length < 2);
    }

    function renderItem(item) {
        const index = menuItems.push(item) - 1;
        if (item.card) {
            const card = item.card;
            const power = (card.power !== null && card.power !== undefined) ? card.power : '?';
            return `
                <button type="button" class="sbx-card-row" data-sbx-item="${index}">
                    <span class="sbx-card-art">${cardArtTag(card.name, 'sbx-art')}</span>
                    <span class="sbx-card-text">
                        <span class="sbx-card-name">${escapeHtml(card.name)}${card.facedown ? ' <span class="sbx-tag">face-down</span>' : ''}</span>
                        <span class="sbx-card-meta">${escapeHtml(typeLabel(card))} · cost ${card.cost ?? '?'} · power ${power}</span>
                        ${item.note ? `<span class="sbx-card-meta">${escapeHtml(item.note)}</span>` : ''}
                    </span>
                </button>`;
        }
        return `
            <button type="button" class="sbx-btn ${item.cls || ''}" data-sbx-item="${index}" ${item.disabled ? 'disabled' : ''}>
                <span class="sbx-btn-icon" aria-hidden="true">${item.icon || ''}</span>
                <span class="sbx-btn-text">
                    <span class="sbx-btn-label">${escapeHtml(item.label)}</span>
                    ${item.note ? `<span class="sbx-btn-note">${escapeHtml(item.note)}</span>` : ''}
                </span>
            </button>`;
    }

    // --- menus ---------------------------------------------------------------

    // The toolbox behind the sandbox card in the hand.
    function toolboxMenu() {
        const you = youId();
        const seat = seatOf(you);
        if (!seat) return null;
        const state = sandboxState();
        const others = playerIds().filter((pid) => pid !== you);
        const aiSeats = deps.aiSeats().map(Number);
        const snap = snapshot();
        const acting = state.acting_player_id;

        return {
            title: 'Sandbox',
            hint: 'Edits go straight into this match. Nothing here counts towards your rating, crowns or quests.',
            groups: [
                {
                    label: 'Your hand',
                    items: [
                        {
                            icon: '➕', label: 'Add any card to your hand',
                            run: () => openPicker({ zone: 'hand', playerId: you }),
                        },
                        {
                            icon: '🂠', label: 'Draw the top card of your deck',
                            note: `${seat.deck.length} card${seat.deck.length === 1 ? '' : 's'} left`,
                            disabled: seat.deck.length === 0,
                            run: () => drawTop(you),
                        },
                        {
                            icon: '🗑', label: 'Discard a card from your hand',
                            disabled: seat.hand.length === 0,
                            run: () => openMenu(() => discardMenu(you)),
                        },
                    ],
                },
                {
                    label: 'Who plays which seat',
                    items: [
                        ...others.map((pid) => ({
                            icon: '🎮', label: `Take control of ${deps.seatLabel(pid)}`,
                            note: 'Play their seat yourself — the board flips to their point of view',
                            run: () => { closeMenu(); deps.controlSeat(pid); },
                        })),
                        ...others.map((pid) => ({
                            icon: aiSeats.includes(pid) ? '🤖' : '🚫',
                            label: `AI plays ${deps.seatLabel(pid)}: ${aiSeats.includes(pid) ? 'on' : 'off'}`,
                            note: aiSeats.includes(pid)
                                ? 'Tap to stop the AI from taking this seat\'s turns'
                                : 'Tap to let the AI take this seat\'s turns again',
                            run: () => { deps.setAiSeat(pid, !aiSeats.includes(pid)); renderMenu(); },
                        })),
                        {
                            icon: '🤖', label: `Let the AI play ${deps.seatLabel(you)}'s turn`,
                            note: acting === you ? '' : 'Only while this seat is the one to act',
                            disabled: acting !== you,
                            run: () => { closeMenu(); deps.aiPlayNow(you); },
                        },
                    ],
                },
                {
                    label: 'Look inside a seat',
                    items: playerIds().map((pid) => ({
                        icon: '🗂', label: `${label(pid)}: hand, deck, underworld, mana`,
                        run: () => openMenu(() => seatMenu(pid)),
                    })),
                },
                {
                    label: 'Match',
                    items: [
                        {
                            icon: '↶', label: 'Undo the last step',
                            note: 'Takes back the last edit, play or AI move',
                            disabled: !state.can_undo,
                            run: () => deps.undo(),
                        },
                        ...playerIds()
                            .filter((pid) => pid !== Number(state.current_player_id))
                            .map((pid) => ({
                                icon: '⏭', label: `Give the turn to ${deps.seatLabel(pid)}`,
                                run: () => mutate({ op: 'set_current_player', player_id: pid }),
                            })),
                        ...(state.phase === 'MULLIGAN' ? [{
                            icon: '⏩', label: 'Skip the mulligan',
                            note: 'Straight to the first turn, with the hands as they are',
                            run: () => mutate({ op: 'skip_mulligan' }),
                        }] : []),
                        ...(state.phase === 'GAME_OVER' ? [{
                            icon: '▶', label: 'Play on past the end of the game',
                            note: 'Puts the finished match back into its main phase',
                            run: () => mutate({ op: 'set_phase', value: 'MAIN' }),
                        }] : []),
                        ...(snap && snap.pending_choice ? [{
                            icon: '✕', label: 'Cancel the pending choice',
                            note: `${deps.seatLabel(snap.pending_choice.player_id)} is being asked to choose`,
                            cls: 'danger',
                            run: () => mutate({ op: 'clear_pending_choice' }),
                        }] : []),
                        {
                            icon: '🧪', label: 'Put the sandbox tools away',
                            note: 'The edits stay, and the History sheet brings the tools back',
                            run: () => { closeMenu(); deps.hideTools(); },
                        },
                    ],
                },
            ],
        };
    }

    function discardMenu(playerId) {
        const seat = seatOf(playerId);
        if (!seat || !seat.hand.length) return null;
        return {
            title: 'Discard a card',
            hint: `Sends a card from ${label(playerId)}'s hand to their underworld.`,
            groups: [{
                items: seat.hand.map((card) => ({
                    card,
                    run: () => mutate({ op: 'move_card', card_id: card.id, zone: 'underworld', player_id: playerId }),
                })),
            }],
        };
    }

    function seatMenu(playerId) {
        const seat = seatOf(playerId);
        if (!seat) return null;
        const aiSeats = deps.aiSeats().map(Number);
        const isYou = Number(playerId) === youId();
        const zones = ['hand', 'deck', 'underworld'].concat(seat.set_aside.length ? ['set_aside'] : []);
        return {
            title: label(playerId),
            groups: [
                {
                    label: 'Zones',
                    items: zones.map((zone) => ({
                        icon: ZONE_ICONS[zone],
                        label: `${ZONE_LABELS[zone]} (${seat[zone].length})`,
                        run: () => openMenu(() => zoneMenu(playerId, zone)),
                    })),
                },
                {
                    label: 'Resources',
                    items: [
                        {
                            icon: '💧', label: `Mana: ${seat.mana} of ${seat.mana_cap}`,
                            note: 'Tap the gems on the board to spend or refresh one by one',
                            run: () => openMenu(() => manaMenu(playerId)),
                        },
                        {
                            icon: '👑', label: `Crowns: ${seat.victory_points}`,
                            note: 'Tap the crowns in the score panel to set them one by one',
                            run: () => openMenu(() => crownMenu(playerId)),
                        },
                    ],
                },
                {
                    label: 'Control',
                    items: [
                        ...(isYou ? [] : [{
                            icon: '🎮', label: 'Take control of this seat',
                            run: () => { closeMenu(); deps.controlSeat(playerId); },
                        }, {
                            icon: aiSeats.includes(Number(playerId)) ? '🤖' : '🚫',
                            label: `AI plays this seat: ${aiSeats.includes(Number(playerId)) ? 'on' : 'off'}`,
                            run: () => { deps.setAiSeat(playerId, !aiSeats.includes(Number(playerId))); renderMenu(); },
                        }]),
                        ...(isYou ? [{
                            icon: '🤖', label: 'Let the AI play this turn',
                            disabled: sandboxState().acting_player_id !== Number(playerId),
                            run: () => { closeMenu(); deps.aiPlayNow(playerId); },
                        }] : []),
                    ],
                },
            ],
        };
    }

    function zoneMenu(playerId, zone) {
        const seat = seatOf(playerId);
        if (!seat) return null;
        const cards = seat[zone] || [];
        const hint = zone === 'deck'
            ? 'In draw order — the first card is the top of the deck.'
            : `${cards.length} card${cards.length === 1 ? '' : 's'} in ${label(playerId)}'s ${ZONE_LABELS[zone].toLowerCase()}.`;
        return {
            title: `${label(playerId)} — ${ZONE_LABELS[zone]}`,
            hint,
            groups: [
                {
                    items: [
                        {
                            icon: '➕', label: `Add any card to this ${ZONE_LABELS[zone].toLowerCase()}`,
                            run: () => openPicker({ zone, playerId }),
                        },
                        ...(zone === 'deck' ? [
                            {
                                icon: '🂠', label: 'Draw the top card into hand',
                                disabled: cards.length === 0,
                                run: () => drawTop(playerId),
                            },
                            {
                                icon: '🔀', label: 'Shuffle this deck',
                                disabled: cards.length < 2,
                                run: () => mutate({
                                    op: 'shuffle_deck', player_id: playerId,
                                    seed: Math.floor(Math.random() * 1_000_000),
                                }),
                            },
                        ] : []),
                        {
                            icon: '🗑', label: `Empty this ${ZONE_LABELS[zone].toLowerCase()}`,
                            cls: 'danger',
                            disabled: cards.length === 0,
                            run: () => mutate({ op: 'clear_zone', zone, player_id: playerId }),
                        },
                    ],
                },
                {
                    label: cards.length ? 'Tap a card to move it' : '',
                    items: cards.map((card, index) => ({
                        card,
                        note: zone === 'deck' && index === 0 ? 'top of the deck' : '',
                        run: () => openMenu(() => cardMenu(card.id)),
                    })),
                },
            ],
        };
    }

    function laneMenu(locationId) {
        const loc = locationOf(locationId);
        if (!loc) return null;
        const you = youId();
        const groups = [{
            items: playerIds().map((pid) => ({
                icon: '➕', label: `Add any card to ${label(pid)}'s side`,
                run: () => openPicker({ zone: 'location', playerId: pid, locationId }),
            })).concat([{
                icon: '🛡',
                label: protectedHere(you, locationId)
                    ? 'Stop protecting this location for you'
                    : 'Protect this location for you',
                note: 'A protected location is spared by the flood',
                run: () => mutate({
                    op: 'set_protected_location', player_id: you,
                    value: protectedHere(you, locationId) ? null : Number(locationId),
                }),
            }]),
        }];
        for (const pid of playerIds()) {
            const cards = (loc.stacks || {})[String(pid)] || [];
            groups.push({
                label: `${label(pid)}'s side (${cards.length})`,
                items: cards.map((card) => ({
                    card,
                    run: () => openMenu(() => cardMenu(card.id)),
                })).concat(cards.length ? [{
                    icon: '🗑', label: `Clear ${label(pid)}'s side`,
                    cls: 'danger',
                    run: () => mutate({ op: 'clear_zone', zone: 'location', player_id: pid, location_id: Number(locationId) }),
                }] : []),
            });
        }
        return {
            title: laneName(locationId),
            hint: `${loc.total_cards} of ${loc.capacity} slots used.`,
            groups,
        };
    }

    function protectedHere(playerId, locationId) {
        const seat = seatOf(playerId);
        return Boolean(seat) && Number(seat.protected_location) === Number(locationId);
    }

    function cardMenu(cardId) {
        const found = findCard(cardId);
        if (!found) return null;
        const { card, zone, playerId, locationId } = found;
        const owner = Number(card.owner_player_id);
        const modifier = Number(((seatOf(owner) || {}).power_modifiers || {})[cardId] || 0);
        const where = zone === 'location'
            ? `in play at ${laneName(locationId)}, on ${label(playerId)}'s side`
            : `in ${label(playerId)}'s ${ZONE_LABELS[zone].toLowerCase()}`;

        const move = (ops) => () => mutate(ops);
        const groups = [
            {
                items: [{
                    icon: '🔍', label: 'Look at this card',
                    run: () => { closeMenu(); deps.openInspector(card); },
                }],
            },
            {
                label: 'Move it',
                items: [
                    ...playerIds().map((pid) => ({
                        icon: '✋',
                        label: pid === owner ? `Return to ${label(pid)}'s hand` : `Give to ${label(pid)}'s hand`,
                        run: move({ op: 'move_card', card_id: cardId, zone: 'hand', player_id: pid }),
                    })),
                    {
                        icon: '🂠', label: `On top of ${label(owner)}'s deck`,
                        run: move({ op: 'move_card', card_id: cardId, zone: 'deck', player_id: owner, index: 0 }),
                    },
                    {
                        icon: '🂠', label: `Under ${label(owner)}'s deck`,
                        run: move({ op: 'move_card', card_id: cardId, zone: 'deck', player_id: owner }),
                    },
                    {
                        icon: '🕯', label: `To ${label(owner)}'s underworld`,
                        run: move({ op: 'move_card', card_id: cardId, zone: 'underworld', player_id: owner }),
                    },
                    {
                        icon: '✦', label: 'Banish out of the game',
                        cls: 'danger',
                        run: move({ op: 'remove_card', card_id: cardId }),
                    },
                ],
            },
            {
                label: 'Put it in play',
                items: (sandboxState().locations || []).flatMap((loc) => playerIds().map((pid) => ({
                    icon: '⚔',
                    label: `${laneName(loc.location_id)} — ${label(pid)}'s side`,
                    disabled: zone === 'location' && Number(locationId) === Number(loc.location_id) && pid === playerId,
                    run: move({
                        op: 'move_card', card_id: cardId, zone: 'location',
                        player_id: pid, location_id: Number(loc.location_id),
                    }),
                }))),
            },
            {
                label: 'Tweak it',
                items: [
                    {
                        icon: '＋', label: 'Power +1', note: modifier ? `modifier now ${modifier > 0 ? '+' : ''}${modifier}` : '',
                        run: move({ op: 'set_power_modifier', card_id: cardId, value: modifier + 1 }),
                    },
                    {
                        icon: '−', label: 'Power −1',
                        run: move({ op: 'set_power_modifier', card_id: cardId, value: modifier - 1 }),
                    },
                    {
                        icon: '🙈', label: card.facedown ? 'Turn it face up' : 'Turn it face down',
                        run: move({ op: 'set_facedown', card_id: cardId, value: !card.facedown }),
                    },
                ],
            },
        ];
        return { title: card.name, hint: `${where}. Owned by ${label(owner)}.`, groups };
    }

    function manaMenu(playerId) {
        const seat = seatOf(playerId);
        if (!seat) return null;
        const setMana = (value) => mutate({ op: 'set_stat', stat: 'mana_pool', player_id: playerId, value });
        const setCap = (value) => mutate([
            { op: 'set_stat', stat: 'player_turn_counts', player_id: playerId, value },
            { op: 'set_stat', stat: 'mana_pool', player_id: playerId, value: Math.min(seat.mana, Math.min(7, value)) },
        ]);
        return {
            title: `${label(playerId)} — mana`,
            hint: `${seat.mana} of ${seat.mana_cap} available. The cap is the number of turns this seat has taken (7 at most).`,
            groups: [{
                items: [
                    { icon: '＋', label: 'One more mana', run: () => setManaFor(playerId, seat.mana + 1) },
                    { icon: '−', label: 'One less mana', disabled: seat.mana === 0, run: () => setMana(Math.max(0, seat.mana - 1)) },
                    { icon: '🔄', label: 'Refresh all mana', disabled: seat.mana >= seat.mana_cap, run: () => setMana(seat.mana_cap) },
                    { icon: '⭘', label: 'Spend all mana', disabled: seat.mana === 0, run: () => setMana(0) },
                    { icon: '＋', label: 'Raise the cap by 1', disabled: seat.turn_count >= 7, run: () => setCap(seat.turn_count + 1) },
                    { icon: '−', label: 'Lower the cap by 1', disabled: seat.turn_count === 0, run: () => setCap(seat.turn_count - 1) },
                ],
            }],
        };
    }

    function crownMenu(playerId) {
        const seat = seatOf(playerId);
        if (!seat) return null;
        const setCrowns = (value) => mutate({ op: 'set_stat', stat: 'victory_points', player_id: playerId, value });
        return {
            title: `${label(playerId)} — crowns`,
            hint: `${seat.victory_points} won so far. The fourth crown ends the game at the next round break.`,
            groups: [{
                items: [
                    { icon: '＋', label: 'One more crown', run: () => setCrowns(seat.victory_points + 1) },
                    { icon: '−', label: 'One less crown', disabled: seat.victory_points === 0, run: () => setCrowns(seat.victory_points - 1) },
                    { icon: '⭘', label: 'No crowns', disabled: seat.victory_points === 0, run: () => setCrowns(0) },
                ],
            }],
        };
    }

    // --- single-tap edits ----------------------------------------------------

    function drawTop(playerId) {
        const seat = seatOf(playerId);
        if (!seat || !seat.deck.length) return null;
        return mutate({ op: 'move_card', card_id: seat.deck[0].id, zone: 'hand', player_id: playerId });
    }

    // Mana above the cap would simply not be shown (the track clamps to the
    // cap), so raising it past the cap raises the cap with it.
    function setManaFor(playerId, value) {
        const seat = seatOf(playerId);
        if (!seat) return null;
        const mana = Math.max(0, Math.min(7, value));
        const ops = [{ op: 'set_stat', stat: 'mana_pool', player_id: playerId, value: mana }];
        if (mana > seat.mana_cap) {
            ops.unshift({ op: 'set_stat', stat: 'player_turn_counts', player_id: playerId, value: mana });
        }
        return mutate(ops);
    }

    function setCrownsFor(playerId, value) {
        return mutate({ op: 'set_stat', stat: 'victory_points', player_id: playerId, value: Math.max(0, value) });
    }

    // --- the card picker -----------------------------------------------------

    async function openPicker(target) {
        await ensureCatalog();
        picker = {
            title: pickerTitle(target),
            query: '',
            onPick: (cardId) => mutate({
                op: 'add_card',
                card_id: cardId,
                zone: target.zone,
                player_id: target.playerId,
                location_id: target.locationId ?? null,
            }),
        };
        ui.sandboxPickerSearch.value = '';
        renderPicker();
        openSheet(ui.sandboxPickerModal);
        ui.sandboxPickerSearch.focus();
    }

    function pickerTitle(target) {
        if (target.zone === 'location') {
            return `Add a card to ${laneName(target.locationId)} — ${label(target.playerId)}'s side`;
        }
        return `Add a card to ${label(target.playerId)}'s ${ZONE_LABELS[target.zone].toLowerCase()}`;
    }

    function renderPicker() {
        if (!picker) return;
        ui.sandboxPickerTitle.textContent = picker.title;
        const query = picker.query.trim().toLowerCase();
        const matches = catalog.filter((card) => {
            if (!query) return true;
            return `${card.name} ${card.type} ${card.subtype} ${card.effect}`.toLowerCase().includes(query);
        }).slice(0, 120);
        ui.sandboxPickerList.innerHTML = matches.length
            ? matches.map((card) => `
                <button type="button" class="sbx-card-row" data-sbx-pick="${escapeHtml(card.id)}">
                    <span class="sbx-card-art">${cardArtTag(card.name, 'sbx-art')}</span>
                    <span class="sbx-card-text">
                        <span class="sbx-card-name">${escapeHtml(card.name)}</span>
                        <span class="sbx-card-meta">${escapeHtml(typeLabel(card))} · cost ${card.cost} · power ${card.power}</span>
                        <span class="sbx-card-meta sbx-card-effect">${escapeHtml(card.effect || '')}</span>
                    </span>
                </button>`).join('')
            : '<div class="sbx-empty">No card matches that search.</div>';
    }

    // --- sheets --------------------------------------------------------------

    function openSheet(modal) {
        modal.classList.add('open');
        modal.setAttribute('aria-hidden', 'false');
    }

    function closeSheet(modal) {
        modal.classList.remove('open');
        modal.setAttribute('aria-hidden', 'true');
    }

    function closePicker() {
        picker = null;
        closeSheet(ui.sandboxPickerModal);
    }

    // --- binding the board ---------------------------------------------------

    // Called after every render: the hand, the lanes, the score panel and the
    // mana tracks are rebuilt from scratch each time (see render.js), so their
    // sandbox handlers are attached to fresh nodes here. The surrounding piles
    // are *not* rebuilt — those are bound once, in init().
    function bind() {
        // The piles keep their outline for as long as the tools are out.
        const on = active();
        pileTargets().forEach(({ el }) => el && el.classList.toggle('sbx-target', on));
        if (!on) return;
        const you = youId();
        const rival = rivalSeat();

        // The sandbox card at the end of the hand: the toolbox.
        ui.hand.querySelectorAll('[data-sandbox-card]').forEach((cardEl) => {
            cardEl.addEventListener('click', (event) => {
                event.stopPropagation();
                openMenu(toolboxMenu);
            });
        });

        // Every location's 🧪 button.
        ui.lanes.querySelectorAll('[data-sandbox-lane]').forEach((btn) => {
            btn.addEventListener('click', (event) => {
                event.stopPropagation();
                event.preventDefault();
                openMenu(() => laneMenu(Number(btn.dataset.sandboxLane)));
            });
        });

        // Mana gems: tap one to spend or refresh exactly that one.
        bindManaTrack(ui.yourMana, you);
        if (rival !== undefined) bindManaTrack(ui.oppMana, rival);
        ui.oppChips.querySelectorAll('.opp-chip[data-player-id]').forEach((chip) => {
            bindManaTrack(chip.querySelector('.chip-mana'), Number(chip.dataset.playerId));
        });

        // Crowns in the score panel (and on the FFA rival chips).
        ui.scorePanel.querySelectorAll('.score-side[data-player-id]').forEach((side) => {
            const pid = Number(side.dataset.playerId);
            side.querySelectorAll('.vp-dot').forEach((dot, index) => {
                dot.classList.add('sbx-target');
                dot.addEventListener('click', (event) => {
                    event.stopPropagation();
                    const current = Number((seatOf(pid) || {}).victory_points || 0);
                    setCrownsFor(pid, index + 1 === current ? index : index + 1);
                });
            });
        });
    }

    // The deck and opponent-hand piles: the elements around the counters, which
    // survive every re-render. Which seat each one belongs to is resolved when
    // it is tapped, since sandbox mode can hand the viewer either side.
    function pileTargets() {
        return [
            { el: pileOf(ui.yourDeckStack), seat: () => youId(), zone: 'deck' },
            { el: pileOf(ui.oppDeckStack), seat: rivalSeat, zone: 'deck' },
            { el: ui.oppHand && ui.oppHand.closest('.peek-hand'), seat: rivalSeat, zone: 'hand' },
        ];
    }

    function rivalSeat() {
        const you = youId();
        return playerIds().find((pid) => pid !== you);
    }

    function pileOf(stackEl) {
        return stackEl ? stackEl.closest('.peek-pile') : null;
    }

    function bindManaTrack(trackEl, playerId) {
        if (!trackEl) return;
        trackEl.querySelectorAll('.mana-gem').forEach((gem, index) => {
            gem.classList.add('sbx-target');
            gem.addEventListener('click', (event) => {
                event.stopPropagation();
                const current = Number((seatOf(playerId) || {}).mana || 0);
                setManaFor(playerId, index + 1 === current ? index : index + 1);
            });
        });
    }

    // The underworld piles and the FFA rival chips already have handlers (they
    // open a read-only stack view); in sandbox mode the controller redirects
    // them here instead.
    function openZone(playerId, zone) {
        openMenu(() => zoneMenu(playerId, zone));
    }

    function openSeat(playerId) {
        openMenu(() => seatMenu(playerId));
    }

    function openToolbox() {
        openMenu(toolboxMenu);
    }

    function init() {
        // Bound once: these piles are part of the page, not of a render.
        pileTargets().forEach(({ el, seat, zone }) => {
            if (!el) return;
            el.addEventListener('click', (event) => {
                if (!active()) return;
                event.stopPropagation();
                const playerId = seat();
                if (playerId !== undefined) openZone(playerId, zone);
            });
        });

        ui.sandboxMenuBody.addEventListener('click', (event) => {
            const btn = event.target.closest('[data-sbx-item]');
            if (!btn || btn.disabled) return;
            const item = menuItems[Number(btn.dataset.sbxItem)];
            if (item && item.run) item.run();
        });
        ui.btnSandboxMenuBack.addEventListener('click', () => backMenu());
        ui.btnCloseSandboxMenu.addEventListener('click', () => closeMenu());
        ui.sandboxMenuModal.addEventListener('click', (event) => {
            if (event.target === ui.sandboxMenuModal) closeMenu();
        });

        ui.sandboxPickerList.addEventListener('click', (event) => {
            const btn = event.target.closest('[data-sbx-pick]');
            if (!btn || !picker) return;
            const { onPick } = picker;
            closePicker();
            onPick(btn.dataset.sbxPick);
        });
        ui.sandboxPickerSearch.addEventListener('input', () => {
            if (!picker) return;
            picker.query = ui.sandboxPickerSearch.value;
            renderPicker();
        });
        ui.btnCloseSandboxPicker.addEventListener('click', () => closePicker());
        ui.sandboxPickerModal.addEventListener('click', (event) => {
            if (event.target === ui.sandboxPickerModal) closePicker();
        });
    }

    return { init, bind, isActive: active, openToolbox, openZone, openSeat, closeAll: () => { closeMenu(); closePicker(); } };
}
