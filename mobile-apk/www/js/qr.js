// A QR encoder, so an invite code can be handed over by pointing one phone at
// another instead of pasting a long string through a chat app.
//
// Written out rather than pulled in: the webapp is plain ES modules with no
// build step and no dependencies, and the Android build bundles this directory
// verbatim. Byte mode and error-correction level M only — that is all a base64
// invite code needs (level M tolerates ~15% damage, which is the usual choice
// for a code read off a screen).
//
// Correctness is checked by decoding what we produce, not by eye — a symbol
// with a subtly wrong format field looks perfectly plausible and scans as
// nothing at all.
//
// Note on masks: the eight candidate masks are scored on the *finished* symbol,
// format modules included, as the spec directs. Some encoders (python-qrcode
// among them) score a matrix carrying placeholder format bits instead and so
// pick a different mask on small symbols. Both produce valid, readable codes —
// the mask in use is recorded in the format field — so a matrix that differs
// from another encoder's is not by itself a bug.

// --- Error correction characteristics (ISO/IEC 18004 table 9, level M) -------
// version -> [ec codewords per block, blocks in group 1, data codewords per
// block in group 1, blocks in group 2, data codewords per block in group 2].
const EC_M = {
    1: [10, 1, 16, 0, 0],
    2: [16, 1, 28, 0, 0],
    3: [26, 1, 44, 0, 0],
    4: [18, 2, 32, 0, 0],
    5: [24, 2, 43, 0, 0],
    6: [16, 4, 27, 0, 0],
    7: [18, 4, 31, 0, 0],
    8: [22, 2, 38, 2, 39],
    9: [22, 3, 36, 2, 37],
    10: [26, 4, 43, 1, 44],
    11: [30, 1, 50, 4, 51],
    12: [22, 6, 36, 2, 37],
    13: [22, 8, 37, 1, 38],
    14: [24, 4, 40, 5, 41],
    15: [24, 5, 41, 5, 42],
    16: [28, 7, 45, 3, 46],
    17: [28, 10, 46, 1, 47],
    18: [26, 9, 43, 4, 44],
    19: [26, 3, 44, 11, 45],
    20: [26, 3, 41, 13, 42],
};

// version -> alignment pattern centre coordinates (empty for version 1).
const ALIGNMENT = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
    11: [6, 30, 54], 12: [6, 32, 58], 13: [6, 34, 62], 14: [6, 26, 46, 66],
    15: [6, 26, 48, 70], 16: [6, 26, 50, 74], 17: [6, 30, 54, 78],
    18: [6, 30, 56, 82], 19: [6, 30, 58, 86], 20: [6, 34, 62, 90],
};

const MAX_VERSION = 20;

function dataCodewords(version) {
    const [, g1, d1, g2, d2] = EC_M[version];
    return g1 * d1 + g2 * d2;
}

// Byte mode spends 4 bits on the mode indicator and 8 (v1-9) or 16 (v10+) on
// the character count.
function byteCapacity(version) {
    const countBits = version < 10 ? 8 : 16;
    return Math.floor((dataCodewords(version) * 8 - 4 - countBits) / 8);
}

// --- GF(256) arithmetic for Reed-Solomon --------------------------------------
const EXP = new Uint8Array(512);
const LOG = new Uint8Array(256);
(() => {
    let x = 1;
    for (let i = 0; i < 255; i += 1) {
        EXP[i] = x;
        LOG[x] = i;
        x <<= 1;
        if (x & 0x100) x ^= 0x11d; // the QR field's primitive polynomial
    }
    for (let i = 255; i < 512; i += 1) EXP[i] = EXP[i - 255];
})();

function gfMul(a, b) {
    if (a === 0 || b === 0) return 0;
    return EXP[LOG[a] + LOG[b]];
}

// Generator polynomial for `degree` error-correction codewords.
function rsGenerator(degree) {
    let poly = [1];
    for (let i = 0; i < degree; i += 1) {
        const next = new Array(poly.length + 1).fill(0);
        for (let j = 0; j < poly.length; j += 1) {
            next[j] ^= poly[j];
            next[j + 1] ^= gfMul(poly[j], EXP[i]);
        }
        poly = next;
    }
    return poly;
}

function rsEncode(data, ecLength) {
    const generator = rsGenerator(ecLength);
    const remainder = new Uint8Array(ecLength);
    for (const byte of data) {
        const factor = byte ^ remainder[0];
        remainder.copyWithin(0, 1);
        remainder[ecLength - 1] = 0;
        for (let i = 0; i < ecLength; i += 1) {
            remainder[i] ^= gfMul(generator[i + 1], factor);
        }
    }
    return remainder;
}

// --- Bit stream ---------------------------------------------------------------
class BitWriter {
    constructor() {
        this.bytes = [];
        this.bitCount = 0;
    }

    push(value, length) {
        for (let i = length - 1; i >= 0; i -= 1) {
            const bit = (value >>> i) & 1;
            const index = this.bitCount >>> 3;
            if (index >= this.bytes.length) this.bytes.push(0);
            if (bit) this.bytes[index] |= 0x80 >>> (this.bitCount & 7);
            this.bitCount += 1;
        }
    }
}

// --- Payload -> final codeword sequence ---------------------------------------
function buildCodewords(bytes, version) {
    const writer = new BitWriter();
    writer.push(0b0100, 4); // byte mode
    writer.push(bytes.length, version < 10 ? 8 : 16);
    for (const byte of bytes) writer.push(byte, 8);

    const capacityBits = dataCodewords(version) * 8;
    // Terminator, then pad to a byte boundary, then the fixed alternating pad.
    writer.push(0, Math.min(4, capacityBits - writer.bitCount));
    while (writer.bitCount % 8 !== 0) writer.push(0, 1);
    const data = writer.bytes.slice();
    const padBytes = [0xec, 0x11];
    for (let i = 0; data.length < dataCodewords(version); i += 1) {
        data.push(padBytes[i % 2]);
    }

    // Split into blocks, compute EC per block, then interleave both.
    const [ecPerBlock, g1, d1, g2, d2] = EC_M[version];
    const blocks = [];
    let offset = 0;
    for (let i = 0; i < g1; i += 1) {
        blocks.push(data.slice(offset, offset + d1));
        offset += d1;
    }
    for (let i = 0; i < g2; i += 1) {
        blocks.push(data.slice(offset, offset + d2));
        offset += d2;
    }
    const ecBlocks = blocks.map((block) => rsEncode(block, ecPerBlock));

    const result = [];
    const longest = Math.max(...blocks.map((b) => b.length));
    for (let i = 0; i < longest; i += 1) {
        for (const block of blocks) if (i < block.length) result.push(block[i]);
    }
    for (let i = 0; i < ecPerBlock; i += 1) {
        for (const ec of ecBlocks) result.push(ec[i]);
    }
    return result;
}

// --- Matrix -------------------------------------------------------------------
function emptyMatrix(size) {
    return {
        size,
        modules: Array.from({ length: size }, () => new Int8Array(size).fill(-1)),
        // Function patterns must not be masked, and must not take data bits.
        reserved: Array.from({ length: size }, () => new Uint8Array(size)),
    };
}

function place(matrix, row, col, value, isFunction = true) {
    matrix.modules[row][col] = value ? 1 : 0;
    if (isFunction) matrix.reserved[row][col] = 1;
}

function drawFinder(matrix, row, col) {
    for (let r = -1; r <= 7; r += 1) {
        for (let c = -1; c <= 7; c += 1) {
            const rr = row + r;
            const cc = col + c;
            if (rr < 0 || cc < 0 || rr >= matrix.size || cc >= matrix.size) continue;
            const inRing = (r >= 0 && r <= 6 && (c === 0 || c === 6))
                || (c >= 0 && c <= 6 && (r === 0 || r === 6));
            const inCore = r >= 2 && r <= 4 && c >= 2 && c <= 4;
            place(matrix, rr, cc, inRing || inCore);
        }
    }
}

function drawAlignment(matrix, version) {
    const centres = ALIGNMENT[version];
    for (const row of centres) {
        for (const col of centres) {
            // The three finder corners have no alignment pattern.
            if ((row === 6 && col === 6)
                || (row === 6 && col === matrix.size - 7)
                || (row === matrix.size - 7 && col === 6)) continue;
            for (let r = -2; r <= 2; r += 1) {
                for (let c = -2; c <= 2; c += 1) {
                    const ring = Math.max(Math.abs(r), Math.abs(c));
                    place(matrix, row + r, col + c, ring !== 1);
                }
            }
        }
    }
}

// The eight format strings for error-correction level M, mask 0-7, MSB first
// (ISO/IEC 18004 table C.1). Only 8 values are ever needed at one EC level, so
// they are tabulated rather than derived — the BCH(15,5) generator plus its
// 0x5412 mask is three lines of code and a dozen ways to be subtly wrong.
const FORMAT_BITS_M = [
    '101010000010010', '101000100100101', '101111001111100', '101101101001011',
    '100010111111001', '100000011001110', '100111110010111', '100101010100000',
];

function bchVersion(version) {
    let value = version << 12;
    for (let i = 17; i >= 12; i -= 1) {
        if ((value >>> i) & 1) value ^= 0x1f25 << (i - 12);
    }
    return (version << 12) | value;
}

// The 15 format bits appear twice. Spelling both copies out as coordinate
// tables (bit index -> module) rather than deriving them: the runs are
// irregular — they hop over the timing row and column — and an off-by-one here
// produces a symbol that looks perfectly plausible and scans as nothing.
const FORMAT_COPY_1 = [
    [8, 0], [8, 1], [8, 2], [8, 3], [8, 4], [8, 5], [8, 7], [8, 8],
    [7, 8], [5, 8], [4, 8], [3, 8], [2, 8], [1, 8], [0, 8],
];

function drawFormat(matrix, mask) {
    const bits = FORMAT_BITS_M[mask]; // MSB first, in the order of the tables below
    const size = matrix.size;
    for (let i = 0; i < 15; i += 1) {
        const bit = bits[i] === '1' ? 1 : 0;
        const [r, c] = FORMAT_COPY_1[i];
        place(matrix, r, c, bit);
        // Second copy: the first seven climb the left edge of the bottom-left
        // finder, the rest run along the top of the bottom-right corner.
        if (i < 7) place(matrix, size - 1 - i, 8, bit);
        else place(matrix, 8, size - 15 + i, bit);
    }
    place(matrix, size - 8, 8, 1); // the always-dark module
}

function drawVersion(matrix, version) {
    if (version < 7) return;
    const bits = bchVersion(version);
    for (let i = 0; i < 18; i += 1) {
        const bit = (bits >>> i) & 1;
        const row = Math.floor(i / 3);
        const col = i % 3;
        place(matrix, row, matrix.size - 11 + col, bit);
        place(matrix, matrix.size - 11 + col, row, bit);
    }
}

function maskBit(mask, row, col) {
    switch (mask) {
        case 0: return (row + col) % 2 === 0;
        case 1: return row % 2 === 0;
        case 2: return col % 3 === 0;
        case 3: return (row + col) % 3 === 0;
        case 4: return (Math.floor(row / 2) + Math.floor(col / 3)) % 2 === 0;
        case 5: return ((row * col) % 2) + ((row * col) % 3) === 0;
        case 6: return (((row * col) % 2) + ((row * col) % 3)) % 2 === 0;
        default: return (((row + col) % 2) + ((row * col) % 3)) % 2 === 0;
    }
}

// Zigzag from the bottom right, two columns at a time, skipping the vertical
// timing column.
function placeData(matrix, codewords, mask) {
    let bitIndex = 0;
    let upward = true;
    let right = matrix.size - 1;
    while (right > 0) {
        // Column 6 is the vertical timing pattern: the pair shifts one to the
        // left and carries on from there, so no column is visited twice.
        if (right === 6) right = 5;
        for (let step = 0; step < matrix.size; step += 1) {
            const row = upward ? matrix.size - 1 - step : step;
            for (const col of [right, right - 1]) {
                if (matrix.reserved[row][col]) continue;
                const byte = codewords[bitIndex >>> 3];
                const bit = byte === undefined ? 0 : (byte >>> (7 - (bitIndex & 7))) & 1;
                place(matrix, row, col, maskBit(mask, row, col) ? bit ^ 1 : bit, false);
                bitIndex += 1;
            }
        }
        upward = !upward;
        right -= 2;
    }
}

// The four penalty rules, used to pick the mask that reads most reliably.
function penalty(matrix) {
    const { size, modules } = matrix;
    let score = 0;

    const runPenalty = (get) => {
        let total = 0;
        for (let a = 0; a < size; a += 1) {
            let run = 1;
            for (let b = 1; b < size; b += 1) {
                if (get(a, b) === get(a, b - 1)) {
                    run += 1;
                } else {
                    if (run >= 5) total += 3 + (run - 5);
                    run = 1;
                }
            }
            if (run >= 5) total += 3 + (run - 5);
        }
        return total;
    };
    score += runPenalty((r, c) => modules[r][c]);
    score += runPenalty((c, r) => modules[r][c]);

    for (let r = 0; r < size - 1; r += 1) {
        for (let c = 0; c < size - 1; c += 1) {
            const v = modules[r][c];
            if (v === modules[r][c + 1] && v === modules[r + 1][c] && v === modules[r + 1][c + 1]) {
                score += 3;
            }
        }
    }

    // Rule 3: the finder-like 1:1:3:1:1 run with four light modules on one
    // side, which a scanner can mistake for a real finder. Counted only where
    // the whole 11-module window lies inside the symbol — treating the edge as
    // light instead would score patterns the spec does not.
    const FINDER_A = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0];
    const FINDER_B = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1];
    const windowMatches = (get, start, pattern) => {
        for (let i = 0; i < 11; i += 1) if (get(start + i) !== pattern[i]) return false;
        return true;
    };
    for (let a = 0; a < size; a += 1) {
        const row = (i) => modules[a][i];
        const col = (i) => modules[i][a];
        for (let b = 0; b + 11 <= size; b += 1) {
            if (windowMatches(row, b, FINDER_A) || windowMatches(row, b, FINDER_B)) score += 40;
            if (windowMatches(col, b, FINDER_A) || windowMatches(col, b, FINDER_B)) score += 40;
        }
    }

    let dark = 0;
    for (let r = 0; r < size; r += 1) for (let c = 0; c < size; c += 1) dark += modules[r][c];
    const percent = (dark * 100) / (size * size);
    score += Math.floor(Math.abs(percent - 50) / 5) * 10;
    return score;
}

function buildMatrix(codewords, version) {
    const size = version * 4 + 17;
    let best = null;
    for (let mask = 0; mask < 8; mask += 1) {
        const matrix = emptyMatrix(size);
        drawFinder(matrix, 0, 0);
        drawFinder(matrix, 0, size - 7);
        drawFinder(matrix, size - 7, 0);
        drawAlignment(matrix, version);
        for (let i = 8; i < size - 8; i += 1) {
            place(matrix, 6, i, i % 2 === 0);
            place(matrix, i, 6, i % 2 === 0);
        }
        drawVersion(matrix, version);
        drawFormat(matrix, mask);
        placeData(matrix, codewords, mask);
        const score = penalty(matrix);
        if (!best || score < best.score) best = { matrix, score };
    }
    return best.matrix;
}

/**
 * Encode `text` as a QR symbol.
 * Returns { size, modules } where modules[row][col] is 1 for a dark module.
 */
export function encodeQr(text) {
    const bytes = new TextEncoder().encode(text);
    let version = 0;
    for (let v = 1; v <= MAX_VERSION; v += 1) {
        if (bytes.length <= byteCapacity(v)) { version = v; break; }
    }
    if (!version) {
        throw new Error(`Too much data for a QR code (${bytes.length} bytes, `
            + `limit ${byteCapacity(MAX_VERSION)}).`);
    }
    const matrix = buildMatrix(buildCodewords(bytes, version), version);
    return { size: matrix.size, modules: matrix.modules, version };
}

/**
 * Render a QR symbol as a standalone SVG string. `moduleSize` is in px and the
 * quiet zone is the 4 modules the spec requires — without it many scanners
 * refuse to read the symbol at all.
 */
export function qrSvg(text, { moduleSize = 4, quiet = 4 } = {}) {
    const { size, modules } = encodeQr(text);
    const dim = (size + quiet * 2) * moduleSize;
    let path = '';
    for (let r = 0; r < size; r += 1) {
        for (let c = 0; c < size; c += 1) {
            if (modules[r][c] === 1) {
                path += `M${(c + quiet) * moduleSize} ${(r + quiet) * moduleSize}`
                    + `h${moduleSize}v${moduleSize}h-${moduleSize}z`;
            }
        }
    }
    // Always black on white regardless of the app's theme: scanners expect the
    // dark modules to be the data, and a themed QR is a QR that will not read.
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${dim}" height="${dim}" `
        + `viewBox="0 0 ${dim} ${dim}" shape-rendering="crispEdges">`
        + `<rect width="${dim}" height="${dim}" fill="#ffffff"/>`
        + `<path d="${path}" fill="#000000"/></svg>`;
}

export const QR_BYTE_LIMIT = byteCapacity(MAX_VERSION);

/**
 * The function patterns of a symbol: finders and their separators, alignment,
 * timing, the format and version fields. `reserved[row][col]` is 1 where a
 * module is one of them and therefore carries no data.
 *
 * The decoder (js/qrdecode.js) needs exactly this to know which modules to read
 * and which to skip, and it is built here — by the encoder's own drawing code,
 * on the encoder's own tables — so the two cannot drift apart. A decoder with
 * its own copy of the layout is a decoder that quietly stops reading the codes
 * this file produces the day one of them is corrected.
 *
 * The mask passed to `drawFormat` is irrelevant: only *which* modules the
 * format field occupies matters here, never what is written in them.
 */
export function functionPatterns(version) {
    const size = version * 4 + 17;
    const matrix = emptyMatrix(size);
    drawFinder(matrix, 0, 0);
    drawFinder(matrix, 0, size - 7);
    drawFinder(matrix, size - 7, 0);
    drawAlignment(matrix, version);
    for (let i = 8; i < size - 8; i += 1) {
        place(matrix, 6, i, i % 2 === 0);
        place(matrix, i, 6, i % 2 === 0);
    }
    drawVersion(matrix, version);
    drawFormat(matrix, 0);
    return matrix;
}

/**
 * The encoder's tables and field arithmetic, shared with the decoder rather
 * than copied into it. Same reasoning as `functionPatterns`: one table, one
 * place to be wrong, one place to fix.
 */
export const qrTables = {
    EC_M, ALIGNMENT, FORMAT_BITS_M, FORMAT_COPY_1, MAX_VERSION,
    EXP, LOG, gfMul, maskBit, dataCodewords,
};
