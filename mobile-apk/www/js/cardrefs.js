// Card names inside effect text are live references: "On enter: Draw
// 'Kur-Jara' and 'Gala-Tura' from your deck" turns those two names into chips
// that show the referenced card (hover on desktop, tap on touch) without
// leaving the card you are reading.
//
// The names are matched against the collection catalog (/api/collection), so
// only cards that actually exist become references — prose that happens to
// look like a name stays plain text.
import { postJson } from './api.js';
import { cardArtTag, effectLabel, escapeHtml, typeLabel } from './helpers.js';

let cardsByName = null; // lowercased name -> card details
let namesByLength = []; // display names, longest first (longest match wins)
let catalogPromise = null;
const catalogReadyCallbacks = new Set();

function indexCatalog(decks) {
    const byName = new Map();
    for (const deck of decks || []) {
        for (const card of deck.cards || []) {
            if (card && card.name && !byName.has(card.name.toLowerCase())) {
                byName.set(card.name.toLowerCase(), card);
            }
        }
    }
    cardsByName = byName;
    // "Dumuzid, Shepherd God" must win over the card literally named
    // "Shepherd" when both start at the same spot, so match longest first.
    namesByLength = Array.from(byName.values())
        .map((card) => card.name)
        .sort((a, b) => b.length - a.length);
    for (const cb of catalogReadyCallbacks) {
        try { cb(); } catch (error) { /* a stale view is not worth breaking on */ }
    }
    catalogReadyCallbacks.clear();
}

// Fetch the card catalog once. Best-effort: without it (offline, LAN guest on a
// flaky link) effect text simply renders as plain text.
export function ensureCardCatalog() {
    if (cardsByName) return Promise.resolve(cardsByName);
    if (!catalogPromise) {
        catalogPromise = postJson('/api/collection', {})
            .then((data) => { indexCatalog(data.decks); return cardsByName; })
            .catch(() => { catalogPromise = null; return null; });
    }
    return catalogPromise;
}

export function cardByName(name) {
    if (!cardsByName || !name) return null;
    return cardsByName.get(String(name).toLowerCase()) || null;
}

// A name only counts as a reference when it stands on its own: "Enkidu" inside
// "Enkidus" is not a mention of the card.
function isBoundary(char) {
    return char === undefined || !/[\p{L}\p{N}]/u.test(char);
}

function findMatchAt(text, lower, index) {
    if (!isBoundary(text[index - 1])) return null;
    for (const name of namesByLength) {
        const end = index + name.length;
        if (lower.startsWith(name.toLowerCase(), index) && isBoundary(text[end])) {
            return { name, end };
        }
    }
    return null;
}

// --- Keyword badges -------------------------------------------------------------
// The trigger a printed effect starts with ("On enter:", "While on top:") is
// shown as a coloured badge with a symbol, so a card's kind of effect reads at a
// glance. First match wins, so the specific triggers sit above the generic
// "When ..." / "At ..." ones.
// Hand-drawn 12x12 glyphs; they take the badge colour through currentColor.
const ICON_STROKE = 'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"';
const ICONS = {
    // an arrow walking into a doorway
    enter: `<path ${ICON_STROKE} d="M1.5 6h5.5M4.8 3.5 7.3 6l-2.5 2.5M9.5 2v8"/>`,
    // a stack of cards, the top one lit
    top: `<rect x="2" y="1.2" width="8" height="3.6" rx="1" fill="currentColor"/><path ${ICON_STROKE} opacity=".6" d="M2.5 7.2h7M2.5 10h7"/>`,
    // a crossed-out mark
    destroy: `<path ${ICON_STROKE} d="M2.5 2.5l7 7M9.5 2.5l-7 7"/>`,
    // an arrow rising from the ground
    revive: `<path ${ICON_STROKE} d="M6 9V2.8M3.4 5.3 6 2.7l2.6 2.6M2.5 10.8h7"/>`,
    // a card with a downward chevron
    draw: `<rect x="2.8" y="1.2" width="6.4" height="9.6" rx="1.2" ${ICON_STROKE}/><path ${ICON_STROKE} d="M4.6 5.3 6 6.8l1.4-1.5"/>`,
    // a loop arrow around a "1"
    once: `<path ${ICON_STROKE} d="M10 6A4 4 0 1 1 8.6 3M8.8 1v2.3H6.5M5.4 5l.9-.6V8"/>`,
    // an hourglass
    timed: `<path ${ICON_STROKE} d="M3.2 1.5h5.6M3.2 10.5h5.6M3.7 1.5c0 3 4.6 3 4.6 4.5s-4.6 1.5-4.6 4.5"/><path ${ICON_STROKE} d="M8.3 1.5c0 3-4.6 3-4.6 4.5s4.6 1.5 4.6 4.5"/>`,
    // a lightning bolt
    when: `<path d="M7 .8 2.8 6.6h2.8L5 11.2l4.4-6H6.6z" fill="currentColor"/>`,
};

const KEYWORDS = [
    { re: /^on enter/i, cls: 'enter' },
    { re: /^while on top/i, cls: 'top' },
    { re: /^on (destruction|death|leave)/i, cls: 'destroy' },
    { re: /^on revive/i, cls: 'revive' },
    { re: /^on draw/i, cls: 'draw' },
    { re: /^once per turn/i, cls: 'once' },
    { re: /^at the (start|end) of/i, cls: 'timed' },
    { re: /^when/i, cls: 'when' },
];
// "<Trigger text>:" at the start of the effect or of a new sentence/line.
const KEYWORD_RE = /(^|[.!?]\s+|\n)([A-Z][^:.\n]{2,60}):/g;

function keywordBadge(label) {
    const kw = KEYWORDS.find((k) => k.re.test(label));
    if (!kw) return null;
    return `<span class="kw kw-${kw.cls}"><svg class="kw-icon" viewBox="0 0 12 12" aria-hidden="true">${ICONS[kw.cls]}</svg>${escapeHtml(label)}</span>`;
}

// Split `source` into plain pieces and keyword badges; `plain` renders a piece.
function renderWithKeywords(source, plain) {
    let html = '';
    let from = 0;
    for (const m of source.matchAll(KEYWORD_RE)) {
        const badge = keywordBadge(m[2]);
        if (!badge) continue;
        const labelStart = m.index + m[1].length;
        html += plain(source.slice(from, labelStart)) + badge;
        from = labelStart + m[2].length + 1; // past the colon
    }
    return html + plain(source.slice(from));
}

// Effect text with every catalog card name wrapped in a reference chip and its
// leading trigger shown as a keyword badge.
// `selfName` (the card being read) is skipped: a card never links to itself.
export function linkifyEffectHtml(text, selfName = '') {
    const source = String(text || '');
    if (!source) return '';
    return renderWithKeywords(source, (piece) => linkifyPlain(piece, selfName));
}

function linkifyPlain(source, selfName) {
    if (!cardsByName || !source) return escapeHtml(source);

    const skip = String(selfName || '').toLowerCase();
    const lower = source.toLowerCase();
    let html = '';
    let plainFrom = 0;
    let i = 0;
    while (i < source.length) {
        const match = findMatchAt(source, lower, i);
        if (!match) {
            i += 1;
            continue;
        }
        if (match.name.toLowerCase() === skip) {
            // Step past the whole self-mention: no piece of a card's own name
            // should link to some shorter card either.
            i = match.end;
            continue;
        }
        html += escapeHtml(source.slice(plainFrom, i));
        const label = source.slice(i, match.end);
        html += `<button type="button" class="card-ref" data-card-ref="${escapeHtml(match.name)}">${escapeHtml(label)}</button>`;
        plainFrom = match.end;
        i = match.end;
    }
    return html + escapeHtml(source.slice(plainFrom));
}

// Write effect text into `el` with its card references live. The text lands
// immediately as plain text and upgrades in place once the catalog arrives, so
// a slow (or failed) catalog fetch never delays or blanks the effect.
export function setEffectText(el, text, selfName = '') {
    if (!el) return;
    const source = String(text || '');
    el.textContent = source;
    el.dataset.effectSource = source;
    if (cardsByName) {
        el.innerHTML = linkifyEffectHtml(source, selfName);
        return;
    }
    ensureCardCatalog().then(() => {
        // The element may have been reused for another card in the meantime.
        if (!cardsByName || el.dataset.effectSource !== source) return;
        el.innerHTML = linkifyEffectHtml(source, selfName);
    });
}

// Re-link every effect element under `root` that carries its source text
// (data-effect-source). Markup rendered as one HTML string can't await the
// catalog, so it renders plain and calls this once the catalog is in.
export function upgradeEffectElements(root) {
    if (!cardsByName || !root) return;
    for (const el of root.querySelectorAll('[data-effect-source]')) {
        el.innerHTML = linkifyEffectHtml(el.dataset.effectSource, el.dataset.effectSelf || '');
    }
}

// --- The preview popup -------------------------------------------------------

let previewEl = null;
let pinnedRef = null; // the chip whose preview a tap opened (touch input)

function previewNode() {
    if (previewEl) return previewEl;
    previewEl = document.createElement('div');
    previewEl.className = 'card-ref-preview';
    previewEl.setAttribute('aria-hidden', 'true');
    document.body.appendChild(previewEl);
    return previewEl;
}

function previewHtml(card) {
    const cost = card.cost ?? '?';
    const power = (card.power !== null && card.power !== undefined) ? card.power : '?';
    const type = typeLabel(card);
    return `
        <div class="card-headline">
            <span class="stat-badge cost">${escapeHtml(String(cost))}</span>
            <div class="card-title">${escapeHtml(card.name)}</div>
            <span class="stat-badge power">${escapeHtml(String(power))}</span>
        </div>
        ${type ? `<div class="card-type">${escapeHtml(type)}</div>` : ''}
        <div class="card-ref-preview-media">${cardArtTag(card.name, 'card-ref-preview-art', { eager: true })}</div>
        <div class="card-ref-preview-effect tiny">${linkifyEffectHtml(effectLabel(card), card.name)}</div>
    `;
}

function placePreview(anchor) {
    const node = previewNode();
    const anchorRect = anchor.getBoundingClientRect();
    const rect = node.getBoundingClientRect();
    const margin = 8;
    let left = anchorRect.left + (anchorRect.width / 2) - (rect.width / 2);
    left = Math.max(margin, Math.min(left, window.innerWidth - rect.width - margin));
    // Above the chip by default; below it when there is no room up there.
    let top = anchorRect.top - rect.height - margin;
    if (top < margin) top = Math.min(anchorRect.bottom + margin, window.innerHeight - rect.height - margin);
    node.style.left = `${Math.round(left)}px`;
    node.style.top = `${Math.round(Math.max(margin, top))}px`;
}

function showPreview(anchor) {
    const card = cardByName(anchor.dataset.cardRef);
    if (!card) return;
    const node = previewNode();
    node.innerHTML = previewHtml(card);
    node.classList.add('open');
    node.setAttribute('aria-hidden', 'false');
    placePreview(anchor);
}

export function hideCardRefPreview() {
    pinnedRef = null;
    if (!previewEl) return;
    previewEl.classList.remove('open');
    previewEl.setAttribute('aria-hidden', 'true');
}

// Wire the document-level handlers once. Delegation means every effect text —
// the inspector, the card-stack popup, the collection — gets references for
// free, including the ones rendered later.
export function initCardRefs() {
    ensureCardCatalog();

    document.addEventListener('mouseover', (event) => {
        const ref = event.target.closest && event.target.closest('.card-ref');
        if (!ref || pinnedRef) return;
        showPreview(ref);
    });

    document.addEventListener('mouseout', (event) => {
        const ref = event.target.closest && event.target.closest('.card-ref');
        if (!ref || pinnedRef) return;
        hideCardRefPreview();
    });

    // A tap pins the preview (touch has no hover); tapping the same chip again,
    // or anywhere else, dismisses it. The click never reaches the card tile
    // underneath, so reading a reference can't re-trigger the host popup.
    document.addEventListener('click', (event) => {
        const ref = event.target.closest && event.target.closest('.card-ref');
        if (ref) {
            event.preventDefault();
            event.stopPropagation();
            if (pinnedRef === ref) {
                hideCardRefPreview();
                return;
            }
            pinnedRef = null;
            showPreview(ref);
            pinnedRef = ref;
            return;
        }
        if (pinnedRef) hideCardRefPreview();
    }, true);

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') hideCardRefPreview();
    });

    window.addEventListener('resize', hideCardRefPreview);
    window.addEventListener('scroll', hideCardRefPreview, true);
}
