// A tiny on-device text embedding model (no network, no dependencies):
// tf-idf weighted word tokens + character trigrams, feature-hashed into a
// fixed-dimensional vector space, compared by cosine similarity.
//
// Two consumers:
//   createCardSearch      - typo-tolerant card name search for the collection
//                           (trigram overlap survives misspellings).
//   createCardRecommender - deck-building recommendations: cards are embedded
//                           from name + type + subtype + effect text, a deck
//                           is the normalized centroid of its card vectors,
//                           and candidates are ranked by cosine similarity.
//                           "The Ark" talks about humans, humans carry the
//                           Human subtype, so their vectors align — no
//                           hand-written if/else rules anywhere.

const DIM = 256;

// FNV-1a: a stable, fast string hash for feature hashing.
function hashToken(token) {
    let h = 0x811c9dc5;
    for (let i = 0; i < token.length; i += 1) {
        h ^= token.charCodeAt(i);
        h = Math.imul(h, 0x01000193);
    }
    return (h >>> 0) % DIM;
}

function normalizeText(text) {
    return String(text || '')
        .toLowerCase()
        .normalize('NFD')
        .replace(/[̀-ͯ]/g, '') // fold accents: Šara ~ sara
        .replace(/[^a-z0-9\s'-]/g, ' ');
}

function wordTokens(text) {
    return normalizeText(text).split(/[\s'-]+/).filter((w) => w.length > 1);
}

// Character trigrams with word-boundary markers: "ark" -> ^ar, ark, rk$.
function trigramTokens(text) {
    const grams = [];
    for (const word of wordTokens(text)) {
        const padded = `^${word}$`;
        for (let i = 0; i + 3 <= padded.length; i += 1) {
            grams.push(`#${padded.slice(i, i + 3)}`);
        }
    }
    return grams;
}

function allTokens(text) {
    return [...wordTokens(text), ...trigramTokens(text)];
}

function l2normalize(vec) {
    let norm = 0;
    for (let i = 0; i < vec.length; i += 1) norm += vec[i] * vec[i];
    norm = Math.sqrt(norm);
    if (norm > 0) {
        for (let i = 0; i < vec.length; i += 1) vec[i] /= norm;
    }
    return vec;
}

export function cosineSim(a, b) {
    let dot = 0;
    for (let i = 0; i < a.length; i += 1) dot += a[i] * b[i];
    return dot;
}

// An embedder fitted on a corpus: tokens that appear in many documents
// (e.g. "on", "enter", common trigrams) are downweighted by inverse document
// frequency so distinctive words like "human" or "underworld" dominate.
export function createEmbedder(corpusTexts) {
    const docFreq = new Map();
    const docCount = Math.max(1, (corpusTexts || []).length);
    for (const text of (corpusTexts || [])) {
        for (const token of new Set(allTokens(text))) {
            docFreq.set(token, (docFreq.get(token) || 0) + 1);
        }
    }

    function idf(token) {
        const df = docFreq.get(token) || 0;
        return Math.log(1 + docCount / (1 + df));
    }

    function embed(text) {
        const vec = new Float32Array(DIM);
        const counts = new Map();
        for (const token of allTokens(text)) {
            counts.set(token, (counts.get(token) || 0) + 1);
        }
        for (const [token, count] of counts) {
            // Sub-linear tf so a word repeated five times doesn't drown the rest.
            const weight = (1 + Math.log(count)) * idf(token);
            vec[hashToken(token)] += weight;
        }
        return l2normalize(vec);
    }

    return { embed };
}

// --- Card search ---------------------------------------------------------------
// Exact fuzzy matching instead of hashed vectors: the card pool is tiny, so we
// can afford real edit distances per word. No hash collisions means a typo like
// "Aiax" ranks Ajax first and never surfaces an unrelated card. Searches cover
// name, type, subtype ("Being", "Hero", ...) and effect text.

function levenshtein(a, b) {
    const m = a.length;
    const n = b.length;
    if (!m) return n;
    if (!n) return m;
    let prev = new Array(n + 1);
    let curr = new Array(n + 1);
    for (let j = 0; j <= n; j += 1) prev[j] = j;
    for (let i = 1; i <= m; i += 1) {
        curr[0] = i;
        for (let j = 1; j <= n; j += 1) {
            const cost = a[i - 1] === b[j - 1] ? 0 : 1;
            curr[j] = Math.min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost);
        }
        [prev, curr] = [curr, prev];
    }
    return prev[n];
}

// How well a single query word matches a single field word, in [0, 1].
function wordSimilarity(queryWord, word) {
    if (word === queryWord) return 1;
    if (word.startsWith(queryWord)) return 0.92;
    if (queryWord.length >= 3 && word.includes(queryWord)) return 0.8;
    if (queryWord.length < 3) return 0;
    const dist = levenshtein(queryWord, word);
    const sim = 1 - dist / Math.max(queryWord.length, word.length);
    // One typo in a short word (Aiax ~ Ajax = 0.75) should clear this bar;
    // genuinely different words should not.
    return sim >= 0.6 ? sim * 0.9 : 0;
}

export function createCardSearch(cards) {
    const entries = cards.map((card) => ({
        card,
        name: normalizeText(card.name || '').trim(),
        nameWords: wordTokens(card.name || ''),
        // Type and subtype are strong, targeted fields; effect text is weaker.
        tagWords: wordTokens([card.type, card.subtype].filter(Boolean).join(' ')),
        effectWords: wordTokens(card.effect || ''),
    }));

    // Returns [{card, score}] sorted by relevance, or null for an empty query.
    return function search(query) {
        const trimmed = String(query || '').trim();
        if (!trimmed) return null;
        const queryLower = normalizeText(trimmed).trim();
        const queryWords = wordTokens(trimmed);
        if (!queryWords.length && !queryLower) return null;

        const scored = entries.map((entry) => {
            let score = 0;
            // Whole-query substring on the name always wins the top spots.
            if (queryLower && entry.name.startsWith(queryLower)) score += 0.5;
            else if (queryLower && entry.name.includes(queryLower)) score += 0.35;

            if (queryWords.length) {
                let total = 0;
                for (const qw of queryWords) {
                    let best = 0;
                    for (const w of entry.nameWords) best = Math.max(best, wordSimilarity(qw, w));
                    for (const w of entry.tagWords) best = Math.max(best, wordSimilarity(qw, w) * 0.95);
                    for (const w of entry.effectWords) best = Math.max(best, wordSimilarity(qw, w) * 0.75);
                    total += best;
                }
                score += total / queryWords.length;
            }
            return { card: entry.card, score };
        });
        scored.sort((a, b) => b.score - a.score);
        return scored.filter((entry) => entry.score >= 0.5);
    };
}

// --- Deck-building recommendations -------------------------------------------

function cardDocument(card) {
    // Subtype appears twice: tribal identity ("Human", "Hero", "Monster") is
    // the strongest synergy signal in this card pool.
    return [card.name, card.type, card.subtype, card.subtype, card.effect]
        .filter(Boolean)
        .join(' ');
}

export function createCardRecommender(cards) {
    const embedder = createEmbedder(cards.map(cardDocument));
    const vecById = new Map(cards.map((card) => [card.id, embedder.embed(cardDocument(card))]));

    // Rank candidate cards by cosine similarity to the deck centroid.
    // Returns [{card, score}], best first. An empty deck has no taste yet.
    return function recommend(deckCards, candidateCards, limit = 8) {
        const deckVecs = (deckCards || [])
            .map((card) => vecById.get(card.id))
            .filter(Boolean);
        if (!deckVecs.length) return [];
        const centroid = new Float32Array(DIM);
        for (const vec of deckVecs) {
            for (let i = 0; i < DIM; i += 1) centroid[i] += vec[i];
        }
        l2normalize(centroid);

        const scored = (candidateCards || [])
            .map((card) => ({ card, score: cosineSim(vecById.get(card.id) || new Float32Array(DIM), centroid) }))
            .filter((entry) => entry.score > 0.05);
        scored.sort((a, b) => b.score - a.score);
        return scored.slice(0, limit);
    };
}
