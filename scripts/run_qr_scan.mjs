// Drive the QR encoder and decoder headless, and report what they managed.
//
// `webapp/js/qr.js` writes symbols and `webapp/js/qrdecode.js` reads them, and
// the only check either one really answers to is whether the pair survives a
// round trip through something that looks like a photograph. There is no
// browser here and no camera: frames are synthesised — the symbol drawn at a
// chosen module size, tilted through a real projective warp, rotated, blurred,
// lit unevenly and speckled with noise — and fed to the decoder as ImageData.
//
// Run directly to see the numbers, or through `tests/test_qr.py`, which is what
// holds them to account:
//
//     node scripts/run_qr_scan.mjs            # human-readable
//     node scripts/run_qr_scan.mjs --json     # the report the test reads

import { encodeQr, qrTables } from '../src/server/webapp/js/qr.js';
import { decodeQr, readModules, FORMAT_TABLE } from '../src/server/webapp/js/qrdecode.js';

// --- A camera, more or less ---------------------------------------------------

function squareToQuad(x0, y0, x1, y1, x2, y2, x3, y3) {
    const dx3 = x0 - x1 + x2 - x3;
    const dy3 = y0 - y1 + y2 - y3;
    if (dx3 === 0 && dy3 === 0) return [x1 - x0, x2 - x1, x0, y1 - y0, y2 - y1, y0, 0, 0, 1];
    const dx1 = x1 - x2;
    const dx2 = x3 - x2;
    const dy1 = y1 - y2;
    const dy2 = y3 - y2;
    const denominator = dx1 * dy2 - dx2 * dy1;
    const a13 = (dx3 * dy2 - dx2 * dy3) / denominator;
    const a23 = (dx1 * dy3 - dx3 * dy1) / denominator;
    return [x1 - x0 + a13 * x1, x3 - x0 + a23 * x3, x0,
        y1 - y0 + a13 * y1, y3 - y0 + a23 * y3, y0, a13, a23, 1];
}

function adjugate(m) {
    const [a, b, c, d, e, f, g, h, i] = m;
    return [e * i - f * h, c * h - b * i, b * f - c * e,
        f * g - d * i, a * i - c * g, c * d - a * f,
        d * h - e * g, b * g - a * h, a * e - b * d];
}

function apply(m, x, y) {
    const w = m[6] * x + m[7] * y + m[8];
    return { x: (m[0] * x + m[1] * y + m[2]) / w, y: (m[3] * x + m[4] * y + m[5]) / w };
}

function blurOnce(image) {
    const { width, height, data } = image;
    const out = new Uint8ClampedArray(data.length);
    for (let y = 0; y < height; y += 1) {
        for (let x = 0; x < width; x += 1) {
            let sum = 0;
            let count = 0;
            for (let dy = -1; dy <= 1; dy += 1) {
                for (let dx = -1; dx <= 1; dx += 1) {
                    const nx = x + dx;
                    const ny = y + dy;
                    if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue;
                    sum += data[(ny * width + nx) * 4];
                    count += 1;
                }
            }
            const at = (y * width + x) * 4;
            out[at] = out[at + 1] = out[at + 2] = sum / count;
            out[at + 3] = 255;
        }
    }
    return { width, height, data: out };
}

/**
 * One frame. `top` is how wide the far edge of the symbol is as a fraction of
 * the near one — a plane seen at an angle — and `gradient` is the light falling
 * across the picture unevenly, which is what defeats a single global threshold.
 */
function frame(symbol, options = {}) {
    const {
        width = 640, height = 480, scale = 6, quiet = 4, top = 1, rotate = 0,
        noise = 0, blur = 0, gradient = 0, offsetX = 0.5, offsetY = 0.5, random = Math.random,
    } = options;
    const side = (symbol.size + quiet * 2) * scale;
    const inverse = adjugate(squareToQuad((1 - top) / 2, 0, (1 + top) / 2, 0, 1, 1, 0, 1));
    const left = (width - side) * offsetX;
    const upper = (height - side) * offsetY;
    const centreX = left + side / 2;
    const centreY = upper + side / 2;
    const data = new Uint8ClampedArray(width * height * 4);
    for (let y = 0; y < height; y += 1) {
        for (let x = 0; x < width; x += 1) {
            let u = x;
            let v = y;
            if (rotate) {
                const s = Math.sin(rotate);
                const c = Math.cos(rotate);
                const dx = x - centreX;
                const dy = y - centreY;
                u = centreX + dx * c + dy * s;
                v = centreY - dx * s + dy * c;
            }
            const lx = (u - left) / side;
            const ly = (v - upper) / side;
            let value = 205; // whatever the symbol is lying on
            if (lx >= 0 && ly >= 0 && lx < 1 && ly < 1) {
                const point = apply(inverse, lx, ly);
                const col = Math.floor((point.x * side) / scale) - quiet;
                const row = Math.floor((point.y * side) / scale) - quiet;
                const dark = row >= 0 && col >= 0 && row < symbol.size && col < symbol.size
                    && symbol.modules[row][col];
                value = dark ? 25 : 240;
            }
            value += gradient * (x / width - 0.5) + (random() * 2 - 1) * noise;
            const at = (y * width + x) * 4;
            data[at] = data[at + 1] = data[at + 2] = value;
            data[at + 3] = 255;
        }
    }
    let image = { width, height, data };
    for (let i = 0; i < blur; i += 1) image = blurOnce(image);
    return image;
}

// Deterministic noise, so a run that fails can be run again and fail the same
// way. (A decoder tuned until one lucky seed passes is a decoder tuned to a
// seed, so the seed is fixed once here rather than per case.)
function seeded(seed) {
    let state = seed >>> 0;
    return () => {
        state = (state * 1664525 + 1013904223) >>> 0;
        return state / 4294967296;
    };
}

// --- The checks ---------------------------------------------------------------

const JOIN_LINK = 'https://mytcg.example/webapp/#join=7QK4F2M9XB';
const ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';

/**
 * The decoder generates the format-information table from the BCH code; the
 * encoder has it written out by hand. They must agree, which is the check
 * qr.js's own comment asks for — a wrong format field produces a symbol that
 * looks perfectly plausible and scans as nothing.
 */
function formatTableAgrees() {
    for (let mask = 0; mask < 8; mask += 1) {
        const entry = FORMAT_TABLE.find((e) => e.data === mask); // level M is 00
        if (entry.bits.toString(2).padStart(15, '0') !== qrTables.FORMAT_BITS_M[mask]) return false;
    }
    return true;
}

function roundTripVersions() {
    const failures = [];
    for (let version = 1; version <= qrTables.MAX_VERSION; version += 1) {
        const countBits = version < 10 ? 8 : 16;
        const capacity = Math.floor((qrTables.dataCodewords(version) * 8 - 4 - countBits) / 8);
        const text = `MYTCG2.${'AbC-19_xyZ'.repeat(300).slice(0, capacity - 7)}`;
        const symbol = encodeQr(text);
        if (symbol.version !== version) continue;
        if (readModules(symbol.modules, version) !== text) failures.push(version);
    }
    return failures;
}

/** How much damage the error correction actually absorbs, in flipped modules. */
function damageTolerated() {
    const symbol = encodeQr(JOIN_LINK);
    let best = 0;
    for (let count = 0; count <= 60; count += 1) {
        const copy = symbol.modules.map((row) => Uint8Array.from(row));
        let done = 0;
        for (let r = 0; r < symbol.size && done < count; r += 3) {
            for (let c = 0; c < symbol.size && done < count; c += 5) {
                if (r < 9 && c < 9) continue; // leave one finder intact
                copy[r][c] ^= 1;
                done += 1;
            }
        }
        if (readModules(copy, symbol.version) !== JOIN_LINK) break;
        best = count;
    }
    return best;
}

function pipelineCases() {
    const long = `MYTCG2.${'AbCd-19_xyZQ'.repeat(14)}`;
    const cases = [
        ['clean', JOIN_LINK, {}],
        ['small modules', JOIN_LINK, { scale: 3 }],
        ['blurred', JOIN_LINK, { blur: 2 }],
        ['noisy', JOIN_LINK, { noise: 45 }],
        ['uneven light', JOIN_LINK, { gradient: 60 }],
        ['rotated', JOIN_LINK, { rotate: (9 * Math.PI) / 180 }],
        ['tilted 25 degrees', JOIN_LINK, { top: 0.72 }],
        ['tilted 40 degrees', JOIN_LINK, { top: 0.55 }],
        ['off centre', JOIN_LINK, { offsetX: 0.15, offsetY: 0.8 }],
        ['long code, clean', long, { scale: 5 }],
        ['long code, blurred', long, { scale: 5, blur: 1 }],
        ['long code, tilted', long, { scale: 5, top: 0.72 }],
    ];
    const results = {};
    let seed = 1;
    for (const [label, text, options] of cases) {
        const symbol = encodeQr(text);
        seed += 1;
        const image = frame(symbol, { ...options, random: seeded(seed) });
        results[label] = decodeQr(image) === text;
    }
    return results;
}

/**
 * The punishing case, as a rate rather than a verdict: small modules, blurred,
 * noisy, unevenly lit, rotated and tilted all at once. A scanner sees frames by
 * the dozen, so what matters is the share that decode, not any single one.
 */
function hardFrameRate() {
    const random = seeded(20260820);
    let decoded = 0;
    const trials = 30;
    for (let i = 0; i < trials; i += 1) {
        const code = Array.from({ length: 10 }, () => ALPHABET[Math.floor(random() * 32)]).join('');
        const text = `https://mytcg.example/webapp/#join=${code}`;
        const symbol = encodeQr(text);
        const image = frame(symbol, {
            scale: 5, blur: 1, noise: 20, gradient: 40, top: 0.85,
            rotate: ((i - 15) * Math.PI) / 180, offsetX: 0.3, offsetY: 0.6, random,
        });
        if (decodeQr(image) === text) decoded += 1;
    }
    return { decoded, trials };
}

function decodeMilliseconds() {
    const symbol = encodeQr(JOIN_LINK);
    const image = frame(symbol, { random: seeded(7) });
    const started = Date.now();
    const runs = 20;
    for (let i = 0; i < runs; i += 1) decodeQr(image);
    return (Date.now() - started) / runs;
}

const report = {
    format_table_agrees: formatTableAgrees(),
    round_trip_failures: roundTripVersions(),
    damage_tolerated: damageTolerated(),
    pipeline: pipelineCases(),
    hard_frames: hardFrameRate(),
    decode_ms: decodeMilliseconds(),
};

if (process.argv.includes('--json')) {
    process.stdout.write(JSON.stringify(report, null, 2));
} else {
    console.log(`format table agrees with the encoder: ${report.format_table_agrees}`);
    console.log(`round-trip failures (versions 1-20): ${report.round_trip_failures.length}`);
    console.log(`flipped modules still recovered: ${report.damage_tolerated}`);
    for (const [label, ok] of Object.entries(report.pipeline)) {
        console.log(`  ${ok ? 'read ' : 'MISS '} ${label}`);
    }
    console.log(`hard frames decoded: ${report.hard_frames.decoded}/${report.hard_frames.trials}`);
    console.log(`decode time: ${report.decode_ms.toFixed(1)} ms per 640x480 frame`);
}
