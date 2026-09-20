// Reads one TypeScript file and prints, as JSON, every exported function: its inputs, what it returns, the helpers and
// constants it relies on, and why it cannot be migrated if it cannot. Static only: nothing in the file is run.
// usage: node scan.cjs <typescript.js> <file.ts>
const fs = require('fs');
const path = require('path');
const ts = require(process.argv[2]);
const file = path.resolve(process.argv[3]);
const program = ts.createProgram([file], {target: ts.ScriptTarget.ES2020, strict: true, noEmit: true});
const checker = program.getTypeChecker();
const source = program.getSourceFile(file);
const SIMPLE = new Set(['number', 'string', 'boolean']);
const OUTSIDE = new Set(['Date', 'console', 'fetch', 'require', 'process', 'setTimeout', 'setInterval', 'globalThis', 'window', 'document',
                         'Intl', 'crypto', 'performance', 'eval', 'Function', 'Reflect', 'Proxy', 'Deno', 'Promise']);

const functions = new Map();      // every top-level function, exported or not
const values = new Map();         // every other top-level name -> the statement that declares it
let imports = false;
for (const s of source.statements) {
  if (ts.isImportDeclaration(s)) imports = true;
  else if (ts.isFunctionDeclaration(s) && s.name) functions.set(s.name.text, s);
  else if (ts.isVariableStatement(s)) for (const d of s.declarationList.declarations) collect(d.name, s);
  else if ((ts.isClassDeclaration(s) || ts.isInterfaceDeclaration(s) || ts.isTypeAliasDeclaration(s) || ts.isEnumDeclaration(s)) && s.name) values.set(s.name.text, s);
}
function collect(name, statement) {
  if (ts.isIdentifier(name)) values.set(name.text, statement);
  else for (const e of name.elements) if (e.name) collect(e.name, statement);
}
const text = node => node.getText(source);
const inputKind = t => SIMPLE.has(t) ? t : /^(readonly )?(number|string|boolean)\[\]$/.test(t) ? t.replace('readonly ', '') : null;
const outputKind = t => /^(number|string|boolean)(\[\])*$/.test(t);

function uses(node) {              // top-level names and outside-world names a function body refers to
  const found = {names: new Set(), outside: new Set()};
  (function visit(n) {
    if (ts.isIdentifier(n) && !(ts.isPropertyAccessExpression(n.parent) && n.parent.name === n)) {
      if (functions.has(n.text) || values.has(n.text)) found.names.add(n.text);
      if (OUTSIDE.has(n.text)) found.outside.add(n.text);
    }
    if (ts.isPropertyAccessExpression(n) && text(n) === 'Math.random') found.outside.add('Math.random');
    ts.forEachChild(n, visit);
  })(node);
  return found;
}

const out = [];
for (const [name, f] of functions) {
  if (!(f.modifiers || []).some(m => m.kind === ts.SyntaxKind.ExportKeyword)) continue;
  // everything it reaches: helpers and constants are shown to the worker; exported functions it calls are separate pieces
  const helpers = new Set(), calls = new Set(), outside = new Set(), queue = [f];
  while (queue.length) {
    const u = uses(queue.pop());
    u.outside.forEach(x => outside.add(x));
    for (const n of u.names) {
      if (n === name || helpers.has(n) || calls.has(n)) continue;
      const other = functions.get(n);
      if (other && (other.modifiers || []).some(m => m.kind === ts.SyntaxKind.ExportKeyword)) calls.add(n);
      else { helpers.add(n); queue.push(other || values.get(n)); }
    }
  }
  const signature = checker.getSignatureFromDeclaration(f);
  const returns = checker.typeToString(checker.getReturnTypeOfSignature(signature));
  const params = f.parameters.map(p => ({name: text(p.name), type: p.type ? inputKind(text(p.type)) : null, said: p.type ? text(p.type) : 'no type',
                                         optional: !!p.questionToken || !!p.initializer, simple: ts.isIdentifier(p.name) && !p.dotDotDotToken}));
  let reason = '';
  if (imports) reason = 'the file imports other modules; only a file that stands alone can be migrated';
  else if ((f.modifiers || []).some(m => m.kind === ts.SyntaxKind.AsyncKeyword) || f.asteriskToken) reason = 'async and generator functions are not supported';
  else if (f.typeParameters) reason = 'generic functions are not supported';
  else if (!f.body) reason = 'it has no body';
  else if (!params.length) reason = 'it takes no inputs, so there is nothing to test it with';
  else if (params.some(p => !p.simple)) reason = 'destructured and rest inputs are not supported';
  else if (params.some(p => p.optional)) reason = `optional input \`${params.find(p => p.optional).name}\` is not supported`;
  else if (params.some(p => !p.type)) { const p = params.find(p => !p.type); reason = `input \`${p.name}\` is ${p.said}; supported: number, string, boolean and arrays of them`; }
  else if (!outputKind(returns)) reason = `it returns ${returns}; supported: number, string, boolean and arrays of them`;
  else if (outside.size) reason = `it uses ${[...outside].join(', ')}, which is not the same on every run or every machine`;
  const order = [...source.statements].filter(s => [...helpers].some(h => functions.get(h) === s || values.get(h) === s));
  out.push({name, line: source.getLineAndCharacterOfPosition(f.getStart(source)).line + 1, params, returns, reason,
            source: text(f), calls: [...calls], needs: order.map(text)});
}
process.stdout.write(JSON.stringify(out));
