// Static scan of one TypeScript file: which top-level `function` declarations could be migrated to
// ArkTS, and what each one needs. Plain syntax analysis (no type checker, no module resolution), run
// with the same pinned TypeScript compiler DevEco uses to build ArkTS, so "supported syntax" always
// means what the real toolchain accepts. Purity/support rules mirror parity/scan_python.py's
// IMPURE_* checks: reject anything whose result could depend on something other than its inputs.
//
// Usage: node scan.cjs <path/to/typescript.js> <file.ts>
// Prints one JSON object to stdout: {"functions": [{name, params, calls, needs, reason, source}]}
'use strict';
const fs = require('fs');

const [, , compilerPath, filePath] = process.argv;
const ts = require(compilerPath);
const text = fs.readFileSync(filePath, 'utf8');
const source = ts.createSourceFile(filePath, text, ts.ScriptTarget.Latest, true);

// Identifiers that make a result depend on something other than its inputs: platform, time,
// randomness, dynamic execution, or the outside world. Mirrors runners/policy.cjs's forbidden set,
// plus a few (Math.random, JSON) not relevant to build-time policy but relevant to purity.
const FORBIDDEN_IDENTIFIERS = new Set([
  'console', 'globalThis', 'process', 'eval', 'Function', 'require', 'fetch', 'setTimeout',
  'setInterval', 'Reflect', 'Proxy', 'Date', 'XMLHttpRequest', 'WorkerThread', 'hilog',
]);
const PURE_MATH_METHODS = new Set([
  'floor', 'ceil', 'round', 'trunc', 'abs', 'min', 'max', 'sqrt', 'cbrt', 'pow', 'sign', 'hypot',
  'log', 'log2', 'log10', 'exp', 'PI', 'E',
]);
const KNOWN_PURE_GLOBALS = new Set(['Math', 'Array', 'String', 'Number', 'Boolean', 'JSON', 'Map', 'Set', 'Object', 'Infinity', 'NaN', 'undefined', 'Error']);

function textOf(node) {
  return text.slice(node.pos, node.end).trim();
}

function fullTextOf(node) {
  // Includes leading comments/JSDoc, the way scan_python.py's `seg()` includes decorators.
  const start = node.getFullStart ? node.getFullStart() : node.pos;
  return text.slice(start, node.end).trim();
}

// ---- type annotations: only number/string/boolean and one level of arrays of those ----
function typeInfo(typeNode) {
  if (!typeNode) return { kind: null, optional: false };
  let node = typeNode;
  let optional = false;
  if (ts.isUnionTypeNode(node)) {
    const isNullish = (t) => t.kind === ts.SyntaxKind.UndefinedKeyword || (ts.isLiteralTypeNode(t) && t.literal.kind === ts.SyntaxKind.NullKeyword);
    const rest = node.types.filter((t) => !isNullish(t));
    if (rest.length === 1 && rest.length < node.types.length) {
      optional = true;
      node = rest[0];
    } else {
      return { kind: null, optional: false };
    }
  }
  if (node.kind === ts.SyntaxKind.NumberKeyword) return { kind: 'number', optional };
  if (node.kind === ts.SyntaxKind.StringKeyword) return { kind: 'string', optional };
  if (node.kind === ts.SyntaxKind.BooleanKeyword) return { kind: 'boolean', optional };
  if (ts.isArrayTypeNode(node)) {
    const inner = typeInfo(node.elementType);
    if (inner.kind && !inner.kind.endsWith('[]')) return { kind: inner.kind + '[]', optional };
  }
  return { kind: null, optional };
}

// ---- default parameter values: only literals we can carry into a JSON range rule ----
function literalValue(node) {
  if (!node) return undefined;
  if (ts.isNumericLiteral(node)) return Number(node.text);
  if (node.kind === ts.SyntaxKind.TrueKeyword) return true;
  if (node.kind === ts.SyntaxKind.FalseKeyword) return false;
  if (ts.isStringLiteralLike(node)) return node.text;
  if (ts.isPrefixUnaryExpression(node) && node.operator === ts.SyntaxKind.MinusToken) {
    const inner = literalValue(node.operand);
    return typeof inner === 'number' ? -inner : undefined;
  }
  if (ts.isArrayLiteralExpression(node) && node.elements.length === 0) return [];
  return undefined;
}

// ---- catalog what the file defines at the top level ----
const functions = new Map(); // name -> FunctionDeclaration
const topLevelNames = new Set(); // anything else declared at top level: consts, classes, imports
const imports = new Set();
for (const statement of source.statements) {
  if (ts.isFunctionDeclaration(statement) && statement.name) {
    functions.set(statement.name.text, statement);
  } else if (ts.isVariableStatement(statement)) {
    for (const decl of statement.declarationList.declarations) {
      if (ts.isIdentifier(decl.name)) topLevelNames.add(decl.name.text);
    }
  } else if (ts.isImportDeclaration(statement)) {
    const clause = statement.importClause;
    if (clause && clause.namedBindings && ts.isNamedImports(clause.namedBindings)) {
      for (const el of clause.namedBindings.elements) imports.add(el.name.text);
    }
    if (clause && clause.name) imports.add(clause.name.text);
  } else if (ts.isClassDeclaration(statement) && statement.name) {
    topLevelNames.add(statement.name.text);
  }
}

function structuralReason(fn) {
  if (fn.modifiers && fn.modifiers.some((m) => m.kind === ts.SyntaxKind.AsyncKeyword)) return 'is async';
  if (fn.asteriskToken) return 'is a generator';
  if (fn.modifiers && fn.modifiers.some((m) => ts.isDecorator && ts.isDecorator(m))) return 'has a decorator, so its real behavior is defined elsewhere';
  if (fn.parameters.some((p) => p.dotDotDotToken)) return 'takes ...rest parameters';
  if (fn.parameters.length === 0) return 'takes no inputs';
  return '';
}

// ---- purity + dependency closure, following calls the way scan_python.py's _closure does ----
function closure(name, seen) {
  const fn = functions.get(name);
  const local = new Set();
  for (const p of fn.parameters) if (ts.isIdentifier(p.name)) local.add(p.name.text);

  const problems = [];
  const calls = [];
  const needs = [];

  function bindLocal(node) {
    // Over-inclusive on purpose: anything bound anywhere inside the body (let/const/for/catch/
    // destructuring) counts as local, even across nested blocks. See docs/TS_ONBOARDING_PLAN.md.
    if (ts.isVariableDeclaration(node) || ts.isBindingElement(node) || ts.isParameter(node)) {
      if (ts.isIdentifier(node.name)) local.add(node.name.text);
    }
    if (ts.isCatchClause(node) && node.variableDeclaration && ts.isIdentifier(node.variableDeclaration.name)) {
      local.add(node.variableDeclaration.name.text);
    }
    ts.forEachChild(node, bindLocal);
  }
  for (const stmt of fn.body ? fn.body.statements : []) bindLocal(stmt);

  function visit(node) {
    if (node.kind === ts.SyntaxKind.ThisKeyword) {
      problems.push('uses this');
      return;
    }
    if (ts.isAwaitExpression(node)) {
      problems.push('is async');
    }
    // Arrow functions / function expressions used as callbacks (e.g. values.map(v => ...)) are
    // walked like any other nested scope: their own parameters were already bound as local above,
    // and every identifier they touch is still checked by the rules below.
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
      const obj = node.expression.expression;
      const method = node.expression.name.text;
      if (ts.isIdentifier(obj) && obj.text === 'Math' && !PURE_MATH_METHODS.has(method)) {
        problems.push(`calls Math.${method}(), which is not deterministic or not supported`);
      }
    }
    if (ts.isElementAccessExpression(node) && node.argumentExpression && ts.isStringLiteralLike(node.argumentExpression)) {
      problems.push('uses dynamic property access');
    }
    if (ts.isIdentifier(node)) {
      const isCallTarget = node.parent && ts.isCallExpression(node.parent) && node.parent.expression === node;
      const isPropertyName = node.parent && (
        (ts.isPropertyAccessExpression(node.parent) && node.parent.name === node) ||
        (ts.isPropertyAssignment(node.parent) && node.parent.name === node) ||
        (ts.isImportSpecifier(node.parent))
      );
      const isBindingName = node.parent && (
        (ts.isVariableDeclaration(node.parent) && node.parent.name === node) ||
        (ts.isBindingElement(node.parent) && node.parent.name === node) ||
        (ts.isParameter(node.parent) && node.parent.name === node)
      );
      if (!isPropertyName && !isBindingName) {
        const nm = node.text;
        if (local.has(nm) || KNOWN_PURE_GLOBALS.has(nm)) {
          // fine
        } else if (nm === name) {
          // recursion into itself: fine, no closure work needed
        } else if (functions.has(nm)) {
          if (!isCallTarget) {
            problems.push(`passes ${nm} as a value, which is not supported yet (call it directly instead)`);
          } else {
            calls.push(nm);
            if (!seen.has(nm)) {
              seen.add(nm);
              const inner = closure(nm, seen);
              problems.push(...inner.problems.map((p) => (p.startsWith('calls ') ? p : `calls ${nm}(), which ${p}`)));
              calls.push(...inner.calls);
              needs.push(...inner.needs, fullTextOf(functions.get(nm)));
            }
          }
        } else if (FORBIDDEN_IDENTIFIERS.has(nm)) {
          problems.push(`uses ${nm}, which reaches outside its inputs`);
        } else if (imports.has(nm)) {
          problems.push(`imports ${nm}; cross-file dependencies are not supported yet`);
        } else if (topLevelNames.has(nm)) {
          problems.push(`reads module-level state (${nm}); shared top-level values are not supported yet`);
        } else {
          problems.push(`uses an unresolved name (${nm})`);
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  for (const stmt of fn.body ? fn.body.statements : []) visit(stmt);
  return { problems, calls: [...new Set(calls)], needs: [...new Set(needs)] };
}

const out = [];
for (const [name, fn] of functions) {
  const params = fn.parameters.map((p) => {
    const info = typeInfo(p.type);
    const hasDefault = !!p.initializer;
    const optional = info.optional || !!p.questionToken;
    return {
      name: ts.isIdentifier(p.name) ? p.name.text : '?',
      type: info.kind,
      optional,
      hasDefault,
      default: hasDefault ? (literalValue(p.initializer) ?? null) : null,
    };
  });
  const entry = { name, params, calls: [], needs: [], reason: '', source: fullTextOf(fn) };
  entry.reason = structuralReason(fn);
  if (!entry.reason) {
    const { problems, calls, needs } = closure(name, new Set([name]));
    entry.calls = calls;
    entry.needs = needs;
    if (problems.length) entry.reason = problems[0];
  }
  out.push(entry);
}
process.stdout.write(JSON.stringify({ functions: out }));
