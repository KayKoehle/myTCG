// A QR *decoder*, so scanning a code works on the phone the player actually has.
//
// The browsers have a built-in one, `BarcodeDetector`, and for a while the Scan
// button was only offered where it exists. That is Chrome on Android and very
// little else: Safari has no BarcodeDetector at all, so on every iPhone the
// button simply was not there — which is what "the scan does not work and it
// never asks for permission" looks like from the outside. There is no
// permission prompt because nothing ever asked for the camera.
//
// So: the camera is opened everywhere it can be opened, `BarcodeDetector` is
// used when it is there because it is faster, and this module reads the frame
// when it is not. Pairs with js/qr.js, which writes the symbols; between them
// the app can hand a code over by eye on any device.
//
// **Scope.** It reads the codes this app produces: error-correction level M,
// versions 1 to 20, byte / numeric / alphanumeric content. Level M is what
// `qr.js` emits, and the tables for the other three levels are three more
// chances to be wrong in a way nothing here would ever exercise. A symbol at
// another level is reported as unreadable rather than guessed at.
//
// **What is actually hard.** Not the decoding — that is bookkeeping — but
// finding the symbol in a photograph of a screen at an angle in bad light. The
// pipeline is the standard one:
//
//   1. Binarize with a local threshold, so a bright corner and a shadowed one
//      are judged separately.
//   2. Find the three finder patterns by their 1:1:3:1:1 signature, checked
//      across the row, the column and both diagonals.
//   3. Work out the symbol's size, and locate the alignment pattern near the
//      fourth corner so that perspective can be undone rather than assumed.
//   4. Map the four known points onto the module grid and sample it.
//   5. Read the format field, unmask, un-interleave the blocks, and let
//      Reed-Solomon repair whatever the camera got wrong.
//
// Step 5 is why a code scans at all from a hand-held phone: a level-M symbol
// tolerates about 15% of its modules being wrong, and most frames are well
// inside that.
//
// **How well it reads.** A frame is either decoded or it is not, and the answer
// arrives in a few milliseconds on a 640x480 image, so what matters is the
// share of frames that succeed rather than any one of them. Every condition on
// its own — out of focus, lit from one side, small in frame, rotated, tilted as
// far as 40 degrees — reads every time. With all of them at once it is most
// frames rather than all, which at video rate is still a lock inside a second.
// `scripts/run_qr_scan.mjs` measures it and `tests/test_qr.py` holds it there.

import { functionPatterns, qrTables } from './qr.js';

const { EC_M, ALIGNMENT, FORMAT_COPY_1, MAX_VERSION, EXP, LOG, gfMul, maskBit } = qrTables;

// --- Binarization -------------------------------------------------------------
// A single threshold over the whole frame fails the moment one corner is lit
// and another is not, which is the normal case for a phone held over a screen.
// So the frame is judged in 8x8 blocks against the average of their
// neighbourhood (the approach ZXing calls hybrid binarization).

const BLOCK = 8;
const BLOCK_NEIGHBOURS = 2; // 5x5 window of blocks around each block
// A block whose lightest and darkest pixels are this close is all one colour —
// paper or ink, not an edge — and thresholding it on its own average would turn
// sensor noise into a checkerboard.
const MIN_DYNAMIC_RANGE = 24;

/** Luma, the cheap way: the eye's weighting of R, G and B. */
function toGrayscale(data, width, height) {
    const gray = new Uint8Array(width * height);
    for (let i = 0; i < gray.length; i += 1) {
        const at = i * 4;
        gray[i] = (data[at] * 77 + data[at + 1] * 150 + data[at + 2] * 29) >> 8;
    }
    return gray;
}

/** `bits[y * width + x]` is 1 for a dark pixel. */
function binarize(gray, width, height) {
    const blocksX = Math.max(1, Math.ceil(width / BLOCK));
    const blocksY = Math.max(1, Math.ceil(height / BLOCK));
    const averages = new Int32Array(blocksX * blocksY);
    const flat = new Uint8Array(blocksX * blocksY); // 1 where the block has no edge in it

    for (let by = 0; by < blocksY; by += 1) {
        for (let bx = 0; bx < blocksX; bx += 1) {
            const x0 = bx * BLOCK;
            const y0 = by * BLOCK;
            const x1 = Math.min(x0 + BLOCK, width);
            const y1 = Math.min(y0 + BLOCK, height);
            let sum = 0;
            let count = 0;
            let min = 255;
            let max = 0;
            for (let y = y0; y < y1; y += 1) {
                for (let x = x0; x < x1; x += 1) {
                    const value = gray[y * width + x];
                    sum += value;
                    count += 1;
                    if (value < min) min = value;
                    if (value > max) max = value;
                }
            }
            averages[by * blocksX + bx] = count ? Math.round(sum / count) : 128;
            flat[by * blocksX + bx] = max - min <= MIN_DYNAMIC_RANGE ? 1 : 0;
        }
    }

    const bits = new Uint8Array(width * height);
    for (let by = 0; by < blocksY; by += 1) {
        for (let bx = 0; bx < blocksX; bx += 1) {
            let sum = 0;
            let count = 0;
            for (let dy = -BLOCK_NEIGHBOURS; dy <= BLOCK_NEIGHBOURS; dy += 1) {
                for (let dx = -BLOCK_NEIGHBOURS; dx <= BLOCK_NEIGHBOURS; dx += 1) {
                    const nx = bx + dx;
                    const ny = by + dy;
                    if (nx < 0 || ny < 0 || nx >= blocksX || ny >= blocksY) continue;
                    sum += averages[ny * blocksX + nx];
                    count += 1;
                }
            }
            let threshold = Math.round(sum / count);
            // An edgeless block takes its verdict from the neighbourhood: if it
            // is markedly lighter than its surroundings it is background, and
            // its own average would otherwise split it down the middle.
            if (flat[by * blocksX + bx] && averages[by * blocksX + bx] > threshold) {
                threshold = averages[by * blocksX + bx];
            }
            const x0 = bx * BLOCK;
            const y0 = by * BLOCK;
            const x1 = Math.min(x0 + BLOCK, width);
            const y1 = Math.min(y0 + BLOCK, height);
            for (let y = y0; y < y1; y += 1) {
                for (let x = x0; x < x1; x += 1) {
                    bits[y * width + x] = gray[y * width + x] < threshold ? 1 : 0;
                }
            }
        }
    }
    return bits;
}

// --- Finding the finder patterns ----------------------------------------------
// The three corner squares are dark:light:dark:light:dark in 1:1:3:1:1 module
// widths through their centre, in every direction. Rows are scanned for that
// run; each hit is confirmed down the column and along both diagonals before it
// counts, because a run of five in one direction alone is something an ordinary
// photograph produces all the time.

function ratioOk(runs) {
    const total = runs[0] + runs[1] + runs[2] + runs[3] + runs[4];
    if (total < 7) return 0;
    const unit = total / 7;
    const slack = unit / 2;
    const ok = Math.abs(unit - runs[0]) < slack
        && Math.abs(unit - runs[1]) < slack
        && Math.abs(unit * 3 - runs[2]) < slack * 3
        && Math.abs(unit - runs[3]) < slack
        && Math.abs(unit - runs[4]) < slack;
    return ok ? unit : 0;
}

/**
 * Walk out from (x, y) along (dx, dy) and measure the five runs through it.
 *
 * Returns the module size *and* where the centre of the middle run actually
 * sits along the walk. The centre matters as much as the verdict: a row scan
 * only knows which row it was on, so its idea of the finder's y is wherever the
 * scan happened to cross it. Sampling a 33-module grid off a centre that is
 * two-thirds of a module out reads the wrong modules and decodes to nothing.
 */
function crossCheck(bits, width, height, x, y, dx, dy) {
    const runs = [0, 0, 0, 0, 0];
    const at = (i) => {
        const px = x + dx * i;
        const py = y + dy * i;
        if (px < 0 || py < 0 || px >= width || py >= height) return -1;
        return bits[py * width + px];
    };
    if (at(0) !== 1) return null;
    // Middle run: the dark centre, both ways from the seed.
    let i = 0;
    while (at(i) === 1) { runs[2] += 1; i -= 1; }
    let j = 1;
    while (at(j) === 1) { runs[2] += 1; j += 1; }
    // Then out through light, dark on each side.
    for (const [step, light, dark] of [[-1, 1, 0], [1, 3, 4]]) {
        let k = step > 0 ? j : i;
        while (at(k) === 0) { runs[light] += 1; k += step; }
        while (at(k) === 1) { runs[dark] += 1; k += step; }
    }
    const unit = ratioOk(runs);
    if (!unit) return null;
    // The middle run covers i+1 .. j-1, so its centre is at (i + j) / 2 steps.
    const middle = (i + j) / 2;
    return { unit, x: x + dx * middle, y: y + dy * middle };
}

function findFinders(bits, width, height) {
    const candidates = [];
    const record = (x, y, unit) => {
        for (const found of candidates) {
            if (Math.abs(found.x - x) <= found.unit && Math.abs(found.y - y) <= found.unit
                && Math.abs(found.unit - unit) <= Math.max(1, found.unit)) {
                // Average the sightings: each row through a finder gives a
                // slightly different centre, and their mean is the real one.
                found.x = (found.x * found.count + x) / (found.count + 1);
                found.y = (found.y * found.count + y) / (found.count + 1);
                found.unit = (found.unit * found.count + unit) / (found.count + 1);
                found.count += 1;
                return;
            }
        }
        candidates.push({ x, y, unit, count: 1 });
    };

    //
    // The five runs are tracked as a rolling window. `stage` counts them off,
    // even stages being dark and odd light, so the colour of a pixel says which
    // run it belongs to and no separate "are we inside a pattern" flag is
    // needed. When five are complete the window slides forward by two rather
    // than starting over — the last three runs of one candidate are the first
    // three of the next, which is how two finders side by side are both seen.
    // Scanned both ways. A row crossing a finder is the usual way one is
    // found, but a noisy frame with small modules breaks runs often enough that
    // some finders are only crossed cleanly in one direction — and a symbol
    // needs all three of them. Columns are cheap and catch the rest.
    //
    // Lines are sampled rather than all scanned: a finder is at least seven
    // modules across, so stepping by two still crosses every one of them
    // several times and halves the work on a 720p frame.
    //
    // The five runs are tracked as a rolling window. `stage` counts them off,
    // even stages being dark and odd light, so the colour of a pixel says which
    // run it belongs to and no separate "are we inside a pattern" flag is
    // needed. When five are complete the window slides forward by two rather
    // than starting over — the last three runs of one candidate are the first
    // three of the next, which is how two finders side by side are both seen.
    for (const down of [false, true]) {
        const lines = down ? width : height;
        const along = down ? height : width;
        const read = down
            ? (line, at) => bits[at * width + line]
            : (line, at) => bits[line * width + at];
        for (let line = 0; line < lines; line += 2) {
            const runs = [0, 0, 0, 0, 0];
            let stage = 0;
            const check = (end) => {
                if (!ratioOk(runs)) return;
                const centre = Math.round(end - runs[4] - runs[3] - runs[2] / 2);
                // The scan gives one coordinate; the perpendicular gives the
                // other, and a second opinion on the module size. Re-checking
                // along the scan direction at that refined position sharpens
                // the first coordinate in turn.
                const seedX = down ? line : centre;
                const seedY = down ? centre : line;
                const first = crossCheck(bits, width, height, seedX, seedY,
                    down ? 1 : 0, down ? 0 : 1);
                if (!first) return;
                const second = crossCheck(bits, width, height,
                    Math.round(first.x), Math.round(first.y),
                    down ? 0 : 1, down ? 1 : 0);
                if (!second) return;
                const centreX = down ? first.x : second.x;
                const centreY = down ? second.y : first.y;
                // A diagonal as well: a run of five across and down happens in
                // ordinary pictures far more often than one in three
                // directions. Either diagonal will do — a symbol at 45 degrees
                // has one of them running along its rings rather than through
                // them, and demanding both would lose it.
                const seed = [Math.round(centreX), Math.round(centreY)];
                if (!crossCheck(bits, width, height, seed[0], seed[1], 1, 1)
                    && !crossCheck(bits, width, height, seed[0], seed[1], 1, -1)) return;
                record(centreX, centreY, (first.unit + second.unit) / 2);
            };
            for (let at = 0; at < along; at += 1) {
                if (read(line, at) === 1) {
                    if (stage % 2 === 1) stage += 1; // light run ended
                    runs[stage] += 1;
                } else if (stage % 2 === 1) {
                    runs[stage] += 1;
                } else if (stage === 4) {
                    check(at);
                    runs[0] = runs[2];
                    runs[1] = runs[3];
                    runs[2] = runs[4];
                    runs[3] = 1;
                    runs[4] = 0;
                    stage = 3;
                } else {
                    stage += 1;
                    runs[stage] += 1;
                }
            }
            if (stage === 4) check(along);
        }
    }
    // A real finder is crossed by many lines; a coincidence usually is not.
    return candidates.filter((c) => c.count >= 2).sort((a, b) => b.count - a.count);
}

function distance(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y);
}

/**
 * Which corner is which. The top-left finder is the one at the right angle, so
 * it is the corner *not* on the longest side; the other two are then told apart
 * by which way the triangle winds.
 */
function orientFinders([a, b, c]) {
    const sides = [
        { length: distance(b, c), apex: a, ends: [b, c] },
        { length: distance(a, c), apex: b, ends: [a, c] },
        { length: distance(a, b), apex: c, ends: [a, b] },
    ].sort((x, y) => y.length - x.length);
    const topLeft = sides[0].apex;
    let [topRight, bottomLeft] = sides[0].ends;
    // Screen coordinates run down, so a correctly-read symbol winds positive.
    const cross = (topRight.x - topLeft.x) * (bottomLeft.y - topLeft.y)
        - (topRight.y - topLeft.y) * (bottomLeft.x - topLeft.x);
    if (cross < 0) [topRight, bottomLeft] = [bottomLeft, topRight];
    return { topLeft, topRight, bottomLeft };
}

/**
 * Modules across the symbol. Measured from the distance between finder centres
 * — 7 modules apart from each edge, so centres are `dimension - 7` apart — then
 * snapped to the only values a QR symbol can have (17 + 4v).
 */
function estimateDimension(topLeft, topRight, bottomLeft, unit) {
    const across = distance(topLeft, topRight) / unit;
    const down = distance(topLeft, bottomLeft) / unit;
    let dimension = Math.round((across + down) / 2) + 7;
    switch (dimension & 0x03) {
        case 0: dimension += 1; break;
        case 2: dimension -= 1; break;
        case 3: return 0; // two away from any legal size: this is not a symbol
        default: break;
    }
    return dimension;
}

/**
 * The dark run through (x, y) along (dx, dy): how long it is, where its centre
 * is, and how much light lies either side of it.
 */
function darkRun(bits, width, height, x, y, dx, dy) {
    const at = (i) => {
        const px = x + dx * i;
        const py = y + dy * i;
        if (px < 0 || py < 0 || px >= width || py >= height) return -1;
        return bits[py * width + px];
    };
    if (at(0) !== 1) return null;
    let i = 0;
    while (at(i) === 1) i -= 1;
    let j = 1;
    while (at(j) === 1) j += 1;
    let before = 0;
    for (let k = i; at(k) === 0; k -= 1) before += 1;
    let after = 0;
    for (let k = j; at(k) === 0; k += 1) after += 1;
    return { length: j - i - 1, centre: (i + j) / 2, lightBefore: before, lightAfter: after };
}

/**
 * The alignment pattern near the fourth corner, which is what makes perspective
 * recoverable: three points fix a plane only if it is flat, and a phone held
 * over a screen never quite is. Returns null when it cannot be found, and the
 * caller falls back to assuming the symbol is a parallelogram — good enough
 * head-on, which is how most codes are actually scanned.
 *
 * The pattern is five modules across, dark ring / light ring / dark centre, so
 * a line through its middle reads dark-light-dark-light-dark. What is looked
 * for is the *inner* three of those — light, dark, light, one module each —
 * because that centre module is the point wanted, and a run either side of it is
 * cheaper to confirm than the whole five.
 */
/**
 * A shortlist, not an answer. The guess at where the pattern should be comes
 * from treating the symbol as a parallelogram, which is exactly the assumption
 * the alignment pattern exists to correct: the more tilted the symbol, the
 * further out the guess, and the further out the guess the more it is needed.
 * So "nearest the guess" is the wrong tie-breaker on precisely the symbols that
 * need one. The caller tries the candidates in turn instead and keeps whichever
 * one decodes — which is a real answer, since a wrong grid does not survive the
 * error correction.
 */
function findAlignment(bits, width, height, expectedX, expectedY, unit) {
    // Widen until there is a real shortlist rather than until something turns
    // up: the first thing a tight window finds on a tilted symbol is usually a
    // coincidence in the data, and the pattern itself is further out than the
    // window reaches. A wider search is a superset, so this ends holding the
    // best set any of the passes could offer.
    let found = [];
    for (const allowance of [4, 8, 16]) {
        found = searchAlignment(bits, width, height, expectedX, expectedY, unit, allowance);
        if (found.length >= MAX_ALIGNMENT_CANDIDATES) break;
    }
    return found;
}

const MAX_ALIGNMENT_CANDIDATES = 6;

function searchAlignment(bits, width, height, expectedX, expectedY, unit, allowance) {
    const span = Math.ceil(unit * allowance);
    const x0 = Math.max(0, Math.round(expectedX - span));
    const x1 = Math.min(width - 1, Math.round(expectedX + span));
    const y0 = Math.max(0, Math.round(expectedY - span));
    const y1 = Math.min(height - 1, Math.round(expectedY + span));
    // A window smaller than the pattern: the guess landed off the frame.
    if (x1 - x0 < 3 || y1 - y0 < 3) return [];
    // The three runs are judged against each other rather than against the
    // module size measured back at the finders: under any real tilt the modules
    // at the far corner are a different size from the ones at the near corner,
    // and that difference is precisely what this pattern is being looked for to
    // measure. `unit` is kept only as a sanity bound.
    const consistent = (a, b, c) => {
        const mean = (a + b + c) / 3;
        if (!(mean > 0) || mean < unit / 2 || mean > unit * 2) return false;
        const slack = Math.max(1, mean * 0.6);
        return Math.abs(a - mean) < slack
            && Math.abs(b - mean) < slack
            && Math.abs(c - mean) < slack;
    };

    const found = [];
    const record = (x, y, score) => {
        // One pattern is crossed by several rows; keep the best sighting of
        // each rather than four rows of the same one.
        for (const seen of found) {
            if (Math.abs(seen.x - x) <= unit && Math.abs(seen.y - y) <= unit) {
                if (score < seen.score) { seen.x = x; seen.y = y; seen.score = score; }
                return;
            }
        }
        found.push({ x, y, score });
    };
    for (let y = y0; y <= y1; y += 1) {
        const runs = [0, 0, 0]; // light, dark, light
        let stage = 0;
        const consider = (endX) => {
            if (!consistent(runs[0], runs[1], runs[2])) return;
            const centreX = endX - runs[2] - runs[1] / 2;
            // The same light-dark-light downwards, which both confirms the
            // sighting and gives the centre's y. Only the *inner* three runs
            // are checked, never the surrounding dark ring: the modules just
            // outside the pattern are ordinary data and are as often dark as
            // not, so a ring that measures wide is not evidence of anything.
            // Requiring both directions is what keeps a run of light-dark-light
            // in the data — which is common — from being taken for a pattern,
            // and a false alignment point is worse than none, since it warps the
            // whole grid where a miss only falls back to treating it as flat.
            const down = darkRun(bits, width, height, Math.round(centreX), y, 0, 1);
            if (!down || !consistent(down.lightBefore, down.length, down.lightAfter)) return;
            const centreY = y + down.centre;
            record(centreX, centreY,
                Math.abs(centreX - expectedX) + Math.abs(centreY - expectedY));
        };
        for (let x = x0; x <= x1; x += 1) {
            if (bits[y * width + x] === 1) {
                if (stage === 2) {
                    consider(x);
                    runs[0] = runs[2];
                    runs[1] = 1;
                    runs[2] = 0;
                    stage = 1;
                } else {
                    stage = 1;
                    runs[1] += 1;
                }
            } else {
                if (stage === 1) stage = 2;
                runs[stage] += 1;
            }
        }
    }
    return found.sort((a, b) => a.score - b.score).slice(0, MAX_ALIGNMENT_CANDIDATES);
}

// --- Perspective --------------------------------------------------------------
// Four known points are enough to undo a projection, which is what a camera
// pointed at a flat screen applies. Three would only give an affine map, which
// cannot express the way the far edge of a tilted symbol is shorter than the
// near one.

function squareToQuad(x0, y0, x1, y1, x2, y2, x3, y3) {
    const dx3 = x0 - x1 + x2 - x3;
    const dy3 = y0 - y1 + y2 - y3;
    if (dx3 === 0 && dy3 === 0) {
        return [x1 - x0, x2 - x1, x0, y1 - y0, y2 - y1, y0, 0, 0, 1];
    }
    const dx1 = x1 - x2;
    const dx2 = x3 - x2;
    const dy1 = y1 - y2;
    const dy2 = y3 - y2;
    const denominator = dx1 * dy2 - dx2 * dy1;
    if (!denominator) return null;
    const a13 = (dx3 * dy2 - dx2 * dy3) / denominator;
    const a23 = (dx1 * dy3 - dx3 * dy1) / denominator;
    return [
        x1 - x0 + a13 * x1, x3 - x0 + a23 * x3, x0,
        y1 - y0 + a13 * y1, y3 - y0 + a23 * y3, y0,
        a13, a23, 1,
    ];
}

function adjugate(m) {
    const [a11, a12, a13, a21, a22, a23, a31, a32, a33] = m;
    return [
        a22 * a33 - a23 * a32, a13 * a32 - a12 * a33, a12 * a23 - a13 * a22,
        a23 * a31 - a21 * a33, a11 * a33 - a13 * a31, a13 * a21 - a11 * a23,
        a21 * a32 - a22 * a31, a12 * a31 - a11 * a32, a11 * a22 - a12 * a21,
    ];
}

function multiply(a, b) {
    const out = new Array(9).fill(0);
    for (let r = 0; r < 3; r += 1) {
        for (let c = 0; c < 3; c += 1) {
            let sum = 0;
            for (let k = 0; k < 3; k += 1) sum += a[r * 3 + k] * b[k * 3 + c];
            out[r * 3 + c] = sum;
        }
    }
    return out;
}

/** The map taking the four grid points onto the four image points. */
function gridToImage(grid, image) {
    const toImage = squareToQuad(...image);
    const toGrid = squareToQuad(...grid);
    if (!toImage || !toGrid) return null;
    return multiply(toImage, adjugate(toGrid));
}

function project(transform, x, y) {
    const [a11, a12, a13, a21, a22, a23, a31, a32, a33] = transform;
    const w = a31 * x + a32 * y + a33;
    if (!w) return null;
    return { x: (a11 * x + a12 * y + a13) / w, y: (a21 * x + a22 * y + a23) / w };
}

/** Read the module grid through the transform: 1 where the module is dark. */
function sampleGrid(bits, width, height, transform, dimension) {
    const modules = Array.from({ length: dimension }, () => new Uint8Array(dimension));
    for (let row = 0; row < dimension; row += 1) {
        for (let col = 0; col < dimension; col += 1) {
            const point = project(transform, col + 0.5, row + 0.5);
            if (!point) return null;
            const x = Math.round(point.x);
            const y = Math.round(point.y);
            if (x < 0 || y < 0 || x >= width || y >= height) return null;
            modules[row][col] = bits[y * width + x];
        }
    }
    return modules;
}

// --- Format information -------------------------------------------------------
// Five bits of content (two of error-correction level, three of mask) carried in
// fifteen, by a BCH code that survives three wrong bits. They are generated here
// rather than tabulated, and `tests/` checks the generated table against the one
// js/qr.js writes with — which is the check the encoder's own comment asks for.

const FORMAT_MASK = 0x5412;

function bchFormat(data) {
    let value = data << 10;
    for (let i = 14; i >= 10; i -= 1) {
        if ((value >>> i) & 1) value ^= 0x537 << (i - 10);
    }
    return ((data << 10) | value) ^ FORMAT_MASK;
}

export const FORMAT_TABLE = (() => {
    const table = [];
    for (let data = 0; data < 32; data += 1) table.push({ data, bits: bchFormat(data) });
    return table;
})();

// The two-bit level field is not in level order: 01 is L, 00 is M, 11 is Q,
// 10 is H (ISO/IEC 18004 table 12).
const LEVELS = { 1: 'L', 0: 'M', 3: 'Q', 2: 'H' };

function popcount(value) {
    let n = value;
    let bits = 0;
    while (n) { bits += n & 1; n >>>= 1; }
    return bits;
}

/** The closest legal format string, or null if none is within three bits. */
function decodeFormat(raw) {
    let best = null;
    for (const entry of FORMAT_TABLE) {
        const difference = popcount(raw ^ entry.bits);
        if (difference === 0) return { level: LEVELS[entry.data >> 3], mask: entry.data & 7 };
        if (!best || difference < best.difference) best = { difference, entry };
    }
    if (!best || best.difference > 3) return null;
    return { level: LEVELS[best.entry.data >> 3], mask: best.entry.data & 7 };
}

function readFormat(modules, dimension) {
    // Copy 1 wraps the top-left finder; its module coordinates are the same
    // table the encoder writes with (js/qr.js), read back in the same order.
    let first = 0;
    for (const [row, col] of FORMAT_COPY_1) first = (first << 1) | modules[row][col];
    // Copy 2 runs up the bottom-left finder and along the top of the
    // bottom-right corner, skipping the always-dark module.
    let second = 0;
    for (let i = 0; i < 7; i += 1) second = (second << 1) | modules[dimension - 1 - i][8];
    for (let i = 0; i < 8; i += 1) second = (second << 1) | modules[8][dimension - 8 + i];
    return decodeFormat(first) || decodeFormat(second);
}

// --- Reading the data ---------------------------------------------------------

/** The zigzag the encoder writes in, run backwards. */
function readCodewords(modules, version, mask) {
    const layout = functionPatterns(version);
    const size = layout.size;
    const bits = [];
    let upward = true;
    let right = size - 1;
    while (right > 0) {
        if (right === 6) right = 5;
        for (let step = 0; step < size; step += 1) {
            const row = upward ? size - 1 - step : step;
            for (const col of [right, right - 1]) {
                if (layout.reserved[row][col]) continue;
                const bit = modules[row][col];
                bits.push(maskBit(mask, row, col) ? bit ^ 1 : bit);
            }
        }
        upward = !upward;
        right -= 2;
    }
    const codewords = [];
    for (let i = 0; i + 8 <= bits.length; i += 8) {
        let byte = 0;
        for (let j = 0; j < 8; j += 1) byte = (byte << 1) | bits[i + j];
        codewords.push(byte);
    }
    return codewords;
}

/** Undo the block interleaving, giving one data+EC block per RS decode. */
function deinterleave(codewords, version) {
    const [ecPerBlock, g1, d1, g2, d2] = EC_M[version];
    const sizes = [];
    for (let i = 0; i < g1; i += 1) sizes.push(d1);
    for (let i = 0; i < g2; i += 1) sizes.push(d2);
    const blocks = sizes.map((size) => new Uint8Array(size));
    const longest = Math.max(...sizes);
    let at = 0;
    for (let i = 0; i < longest; i += 1) {
        for (let b = 0; b < blocks.length; b += 1) {
            if (i < sizes[b]) blocks[b][i] = codewords[at++];
        }
    }
    const ecBlocks = blocks.map(() => new Uint8Array(ecPerBlock));
    for (let i = 0; i < ecPerBlock; i += 1) {
        for (let b = 0; b < blocks.length; b += 1) ecBlocks[b][i] = codewords[at++];
    }
    return blocks.map((data, i) => {
        const joined = new Uint8Array(data.length + ecPerBlock);
        joined.set(data, 0);
        joined.set(ecBlocks[i], data.length);
        return { joined, dataLength: data.length };
    });
}

// --- Reed-Solomon correction --------------------------------------------------
// The encoder's field, run in reverse: work out from the syndromes where the
// errors are and how big they are, and subtract them. The last step is a
// re-check of the syndromes, so a correction that "succeeded" onto the wrong
// codeword — which is what a badly damaged block produces — is caught here
// rather than surfacing as a nonsense payload.

function gfInverse(a) {
    if (a === 0) throw new Error('divide by zero in GF(256)');
    return EXP[255 - LOG[a]];
}

function gfPow(base, exponent) {
    if (base === 0) return 0;
    return EXP[(LOG[base] * exponent) % 255];
}

/** Evaluate a polynomial given highest-degree-first. */
function polyEval(poly, x) {
    let value = 0;
    for (const coefficient of poly) value = gfMul(value, x) ^ coefficient;
    return value;
}

function syndromes(message, count) {
    const out = new Array(count);
    for (let i = 0; i < count; i += 1) out[i] = polyEval(message, EXP[i]);
    return out;
}

/** Berlekamp-Massey: the shortest recurrence the syndromes obey. */
function errorLocator(synd, count) {
    let locator = [1];
    let previous = [1];
    for (let i = 0; i < count; i += 1) {
        let discrepancy = synd[i];
        for (let j = 1; j < locator.length; j += 1) {
            discrepancy ^= gfMul(locator[locator.length - 1 - j], synd[i - j]);
        }
        previous = previous.concat([0]); // multiply by x
        if (discrepancy !== 0) {
            if (previous.length > locator.length) {
                const scaled = previous.map((c) => gfMul(c, discrepancy));
                previous = locator.map((c) => gfMul(c, gfInverse(discrepancy)));
                locator = scaled;
            }
            const scaled = previous.map((c) => gfMul(c, discrepancy));
            const merged = new Array(Math.max(locator.length, scaled.length)).fill(0);
            for (let k = 0; k < locator.length; k += 1) {
                merged[merged.length - locator.length + k] ^= locator[k];
            }
            for (let k = 0; k < scaled.length; k += 1) {
                merged[merged.length - scaled.length + k] ^= scaled[k];
            }
            locator = merged;
        }
    }
    while (locator.length && locator[0] === 0) locator.shift();
    return locator;
}

/**
 * Where the errors are. The locator's roots are the inverses of the error
 * positions, so each candidate is tested at alpha^-i and a hit at i means the
 * codeword `length - 1 - i` is wrong.
 */
function errorPositions(locator, length) {
    const positions = [];
    for (let i = 0; i < length; i += 1) {
        if (polyEval(locator, EXP[(255 - i) % 255]) === 0) positions.push(length - 1 - i);
    }
    return positions;
}

/**
 * How wrong each one is. With the positions known the syndromes are a small
 * linear system — S_j = sum(Y_k * X_k^j) — and its matrix is Vandermonde, so
 * plain elimination over GF(256) always solves it. Shorter than Forney's
 * formula and impossible to get subtly wrong.
 */
function errorMagnitudes(synd, positions, length) {
    const n = positions.length;
    const locations = positions.map((position) => EXP[(length - 1 - position) % 255]);
    const rows = [];
    for (let j = 0; j < n; j += 1) {
        const row = locations.map((x) => gfPow(x, j));
        row.push(synd[j]);
        rows.push(row);
    }
    for (let col = 0; col < n; col += 1) {
        let pivot = col;
        while (pivot < n && rows[pivot][col] === 0) pivot += 1;
        if (pivot === n) return null;
        [rows[col], rows[pivot]] = [rows[pivot], rows[col]];
        const inverse = gfInverse(rows[col][col]);
        for (let k = col; k <= n; k += 1) rows[col][k] = gfMul(rows[col][k], inverse);
        for (let r = 0; r < n; r += 1) {
            if (r === col || rows[r][col] === 0) continue;
            const factor = rows[r][col];
            for (let k = col; k <= n; k += 1) rows[r][k] ^= gfMul(rows[col][k], factor);
        }
    }
    return rows.map((row) => row[n]);
}

/** Repair one block in place, or throw if it is past saving. */
function correct(block, ecCount) {
    const synd = syndromes(block, ecCount);
    if (synd.every((s) => s === 0)) return block;
    const locator = errorLocator(synd, ecCount);
    const errors = locator.length - 1;
    if (errors <= 0 || errors * 2 > ecCount) throw new Error('too damaged');
    const positions = errorPositions(locator, block.length);
    if (positions.length !== errors) throw new Error('too damaged');
    const magnitudes = errorMagnitudes(synd, positions, block.length);
    if (!magnitudes) throw new Error('too damaged');
    const fixed = Uint8Array.from(block);
    positions.forEach((position, i) => { fixed[position] ^= magnitudes[i]; });
    // The check that makes the whole thing trustworthy: a block that still has
    // syndromes was not corrected, it was rewritten into a different wrong one.
    if (!syndromes(fixed, ecCount).every((s) => s === 0)) throw new Error('too damaged');
    return fixed;
}

// --- Bit stream -> text -------------------------------------------------------

const ALPHANUMERIC = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:';

class BitReader {
    constructor(bytes) { this.bytes = bytes; this.at = 0; }
    remaining() { return this.bytes.length * 8 - this.at; }
    read(count) {
        if (count > this.remaining()) throw new Error('ran out of bits');
        let value = 0;
        for (let i = 0; i < count; i += 1) {
            const bit = (this.bytes[this.at >> 3] >> (7 - (this.at & 7))) & 1;
            value = (value << 1) | bit;
            this.at += 1;
        }
        return value;
    }
}

function countBits(mode, version) {
    const tier = version <= 9 ? 0 : (version <= 26 ? 1 : 2);
    if (mode === 1) return [10, 12, 14][tier];
    if (mode === 2) return [9, 11, 13][tier];
    if (mode === 4) return [8, 16, 16][tier];
    throw new Error('unsupported mode');
}

function decodePayload(data, version) {
    const reader = new BitReader(data);
    const bytes = [];
    let text = '';
    while (reader.remaining() >= 4) {
        const mode = reader.read(4);
        if (mode === 0) break; // terminator
        if (mode === 7) { // ECI: skip the assignment number and carry on as UTF-8
            const first = reader.read(8);
            if (first & 0x80) reader.read((first & 0x40) ? 16 : 8);
            continue;
        }
        const count = reader.read(countBits(mode, version));
        if (mode === 4) {
            for (let i = 0; i < count; i += 1) bytes.push(reader.read(8));
        } else if (mode === 2) {
            for (let i = 0; i + 1 < count; i += 2) {
                const pair = reader.read(11);
                text += ALPHANUMERIC[Math.floor(pair / 45)] + ALPHANUMERIC[pair % 45];
            }
            if (count % 2) text += ALPHANUMERIC[reader.read(6)];
        } else if (mode === 1) {
            let left = count;
            while (left >= 3) { text += String(reader.read(10)).padStart(3, '0'); left -= 3; }
            if (left === 2) text += String(reader.read(7)).padStart(2, '0');
            else if (left === 1) text += String(reader.read(4));
        } else {
            throw new Error('unsupported mode');
        }
    }
    // Byte mode is UTF-8 in practice, and always is in the codes this app makes.
    if (bytes.length) text += new TextDecoder().decode(Uint8Array.from(bytes));
    return text;
}

// --- The whole pipeline -------------------------------------------------------

/**
 * Read a QR symbol out of one frame. Returns the decoded text, or null when
 * there is nothing readable in it — which is the ordinary case, several times a
 * second, while the player lines the camera up.
 */
export function decodeQr(imageData) {
    const { width, height } = imageData;
    if (!width || !height) return null;
    const bits = binarize(toGrayscale(imageData.data, width, height), width, height);
    const finders = findFinders(bits, width, height);
    if (finders.length < 3) return null;

    // Try the strongest three; if a fourth candidate looks as good, give the
    // next combination a turn rather than failing on one bad sighting.
    const attempts = [];
    for (let i = 0; i < Math.min(finders.length, 4); i += 1) {
        for (let j = i + 1; j < Math.min(finders.length, 5); j += 1) {
            for (let k = j + 1; k < Math.min(finders.length, 6); k += 1) {
                attempts.push([finders[i], finders[j], finders[k]]);
            }
        }
    }
    for (const trio of attempts.slice(0, 4)) {
        const text = readSymbol(bits, width, height, trio);
        if (text !== null) return text;
    }
    return null;
}

function readSymbol(bits, width, height, trio) {
    const { topLeft, topRight, bottomLeft } = orientFinders(trio);
    const unit = (topLeft.unit + topRight.unit + bottomLeft.unit) / 3;
    if (!(unit > 0)) return null;
    const dimension = estimateDimension(topLeft, topRight, bottomLeft, unit);
    if (!dimension || dimension < 21) return null;
    const version = (dimension - 17) / 4;
    if (!Number.isInteger(version) || version < 1 || version > MAX_VERSION) return null;

    // The far corner. The alignment pattern gives the real one; failing that,
    // the parallelogram's fourth point, which is right when the symbol is
    // square to the camera and progressively wrong as it is not.
    const edge = dimension - 3.5;
    const flatX = topRight.x - topLeft.x + bottomLeft.x;
    const flatY = topRight.y - topLeft.y + bottomLeft.y;
    const corners = [];
    if (ALIGNMENT[version].length) {
        // The alignment centre sits 3 modules in from where a fourth finder
        // centre would have been.
        const inset = 1 - 3 / (dimension - 7);
        for (const found of findAlignment(
            bits, width, height,
            topLeft.x + inset * (flatX - topLeft.x),
            topLeft.y + inset * (flatY - topLeft.y),
            unit,
        )) {
            corners.push({ x: found.x, y: found.y, grid: edge - 3 });
        }
    }
    corners.push({ x: flatX, y: flatY, grid: edge });

    for (const corner of corners) {
        const transform = gridToImage(
            [3.5, 3.5, edge, 3.5, corner.grid, corner.grid, 3.5, edge],
            [topLeft.x, topLeft.y, topRight.x, topRight.y, corner.x, corner.y,
                bottomLeft.x, bottomLeft.y],
        );
        if (!transform) continue;
        const modules = sampleGrid(bits, width, height, transform, dimension);
        if (!modules) continue;
        const text = readModules(modules, version);
        if (text !== null) return text;
    }
    return null;
}

/** Everything from a sampled grid onwards — also the seam the tests drive. */
export function readModules(modules, version) {
    const dimension = version * 4 + 17;
    const format = readFormat(modules, dimension);
    if (!format) return null;
    // Only level M is tabulated (see this file's header): another level is a
    // real QR code that is not one of ours.
    if (format.level !== 'M') return null;

    const codewords = readCodewords(modules, version, format.mask);
    const [ecPerBlock] = EC_M[version];
    const data = [];
    try {
        for (const { joined, dataLength } of deinterleave(codewords, version)) {
            const fixed = correct(joined, ecPerBlock);
            for (let i = 0; i < dataLength; i += 1) data.push(fixed[i]);
        }
        return decodePayload(Uint8Array.from(data), version);
    } catch (error) {
        return null;
    }
}

// Exposed for the tests, which drive the stages without a camera.
export const __testing = { binarize, toGrayscale, findFinders, decodeFormat, correct, bchFormat };
