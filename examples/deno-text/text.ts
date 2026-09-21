// Text functions from the Deno standard library (@std/text), https://github.com/denoland/std/tree/main/text
// Copyright 2018-2026 the Deno authors. MIT license.
// The functions are as published. Seven files are put into this one, so the imports between them, the doc comments
// and the `export` on the two helpers of _util.ts are removed.

// ── _util.ts
const CAPITALIZED_WORD_REGEXP = /\p{Lu}\p{Ll}+/u; // e.g. Apple
const ACRONYM_REGEXP = /\p{Lu}+(?=(\p{Lu}\p{Ll})|\P{L}|\b)/u; // e.g. ID, URL, handles an acronym followed by a capitalized word e.g. HTMLElement
const LOWERCASED_WORD_REGEXP = /(\p{Ll}+)/u; // e.g. apple
const ANY_LETTERS_REGEXP = /\p{L}+/u; // will match any sequence of letters, including in languages without a concept of upper/lower case
const DIGITS_REGEXP = /\p{N}+/u; // e.g. 123

const WORD_OR_NUMBER_REGEXP = new RegExp(
  `${CAPITALIZED_WORD_REGEXP.source}|${ACRONYM_REGEXP.source}|${LOWERCASED_WORD_REGEXP.source}|${ANY_LETTERS_REGEXP.source}|${DIGITS_REGEXP.source}`,
  "gu",
);

function splitToWords(input: string) {
  return input.match(WORD_OR_NUMBER_REGEXP) ?? [];
}

function capitalizeWord(word: string): string {
  return word ? word[0]!.toUpperCase() + word.slice(1).toLowerCase() : word;
}

// ── to_camel_case.ts
export function toCamelCase(input: string): string {
  input = input.trim();
  const [first = "", ...rest] = splitToWords(input);
  return [first.toLowerCase(), ...rest.map(capitalizeWord)].join("");
}

// ── to_kebab_case.ts
export function toKebabCase(input: string): string {
  input = input.trim();
  return splitToWords(input).join("-").toLowerCase();
}

// ── to_pascal_case.ts
export function toPascalCase(input: string): string {
  input = input.trim();
  return splitToWords(input).map(capitalizeWord).join("");
}

// ── to_snake_case.ts
export function toSnakeCase(input: string): string {
  input = input.trim();
  return splitToWords(input).join("_").toLowerCase();
}

// ── unstable_to_constant_case.ts
export function toConstantCase(input: string): string {
  input = input.trim();
  return splitToWords(input).join("_").toUpperCase();
}

// ── levenshtein_distance.ts
const { ceil } = Math;

// This implements Myers' bit-vector algorithm as described here:
// https://dl.acm.org/doi/pdf/10.1145/316542.316550
const peq = new Uint32Array(0x110000);

function myers32(t: string[], p: string[]): number {
  const n = t.length;
  const m = p.length;
  for (let i = 0; i < m; i++) {
    peq[p[i]!.codePointAt(0)!]! |= 1 << i;
  }
  const last = m - 1;
  let pv = -1;
  let mv = 0;
  let score = m;
  for (let j = 0; j < n; j++) {
    const eq = peq[t[j]!.codePointAt(0)!]!;
    const xv = eq | mv;
    const xh = (((eq & pv) + pv) ^ pv) | eq;
    let ph = mv | ~(xh | pv);
    let mh = pv & xh;
    score += ((ph >>> last) & 1) - ((mh >>> last) & 1);
    // Set the horizontal delta in the first row to +1
    // because we are computing the distance between two full strings.
    ph = (ph << 1) | 1;
    mh = mh << 1;
    pv = mh | ~(xv | ph);
    mv = ph & xv;
  }
  for (let i = 0; i < m; i++) {
    peq[p[i]!.codePointAt(0)!] = 0;
  }
  return score;
}

function myersX(t: string[], p: string[]): number {
  const n = t.length;
  const m = p.length;
  // Initialize the horizontal deltas to +1.
  const h = new Int8Array(n).fill(1);
  const bmax = ceil(m / 32) - 1;
  // Process the blocks row by row so that we can use the fixed-size peq array.
  for (let b = 0; b < bmax; b++) {
    const start = b * 32;
    const end = (b + 1) * 32;
    for (let i = start; i < end; i++) {
      peq[p[i]!.codePointAt(0)!]! |= 1 << i;
    }
    let pv = -1;
    let mv = 0;
    for (let j = 0; j < n; j++) {
      const hin = h[j]!;
      let eq = peq[t[j]!.codePointAt(0)!]!;
      const xv = eq | mv;
      eq |= hin >>> 31;
      const xh = (((eq & pv) + pv) ^ pv) | eq;
      let ph = mv | ~(xh | pv);
      let mh = pv & xh;
      h[j] = (ph >>> 31) - (mh >>> 31);
      ph = (ph << 1) | (-hin >>> 31);
      mh = (mh << 1) | (hin >>> 31);
      pv = mh | ~(xv | ph);
      mv = ph & xv;
    }
    for (let i = start; i < end; i++) {
      peq[p[i]!.codePointAt(0)!] = 0;
    }
  }
  const start = bmax * 32;
  for (let i = start; i < m; i++) {
    peq[p[i]!.codePointAt(0)!]! |= 1 << i;
  }
  const last = m - 1;
  let pv = -1;
  let mv = 0;
  let score = m;
  for (let j = 0; j < n; j++) {
    const hin = h[j]!;
    let eq = peq[t[j]!.codePointAt(0)!]!;
    const xv = eq | mv;
    eq |= hin >>> 31;
    const xh = (((eq & pv) + pv) ^ pv) | eq;
    let ph = mv | ~(xh | pv);
    let mh = pv & xh;
    score += ((ph >>> last) & 1) - ((mh >>> last) & 1);
    ph = (ph << 1) | (-hin >>> 31);
    mh = (mh << 1) | (hin >>> 31);
    pv = mh | ~(xv | ph);
    mv = ph & xv;
  }
  for (let i = start; i < m; i++) {
    peq[p[i]!.codePointAt(0)!] = 0;
  }
  return score;
}

export function levenshteinDistance(str1: string, str2: string): number {
  let t = [...str1];
  let p = [...str2];

  if (t.length < p.length) {
    [p, t] = [t, p];
  }
  if (p.length === 0) {
    return t.length;
  }
  return p.length <= 32 ? myers32(t, p) : myersX(t, p);
}
