// Parses every candidate file before DevEco runs any build tooling. A scope rule, not an OS sandbox.
// Allowed at the top of a file: functions, constants, classes, interfaces, types, and imports of the other migrated functions.
// usage: node policy.cjs <typescript.js> <target dir>
const fs = require('fs');
const path = require('path');
const ts = require(process.argv[2]);
const root = process.argv[3];
const names = Object.keys(JSON.parse(fs.readFileSync(path.join(__dirname, 'functions.json'), 'utf8')).functions);
const forbidden = new Set(['eval', 'Function', 'require', 'globalThis', 'console', 'hilog', 'Reflect', 'Proxy', 'Date', 'setTimeout', 'setInterval', 'fetch']);
for (const name of names) {
  const source = ts.createSourceFile(name + '.ts', fs.readFileSync(path.join(root, name + '.ets'), 'utf8'), ts.ScriptTarget.Latest, true);
  if (source.parseDiagnostics.length) throw Error('Candidate parse failed: ' + name + '.ets');
  for (const s of source.statements) {
    if (ts.isImportDeclaration(s)) {
      const from = s.moduleSpecifier.text, c = s.importClause;
      const ok = from.startsWith('./') && names.includes(from.slice(2)) && from.slice(2) !== name && c && !c.name && c.namedBindings && ts.isNamedImports(c.namedBindings)
        && c.namedBindings.elements.every(e => !e.propertyName && e.name.text === from.slice(2));
      if (!ok) throw Error('Unapproved import in ' + name + '.ets: only  import { other } from "./other"  for another migrated function');
    } else if (!(ts.isFunctionDeclaration(s) || ts.isVariableStatement(s) || ts.isClassDeclaration(s) || ts.isInterfaceDeclaration(s) || ts.isTypeAliasDeclaration(s) || ts.isEnumDeclaration(s))) {
      throw Error('Only declarations are permitted at the top of ' + name + '.ets');
    }
  }
  (function visit(node) {
    if (node.kind === ts.SyntaxKind.ImportKeyword || ts.isExportDeclaration(node) || ts.isElementAccessExpression(node) && ts.isStringLiteral(node.argumentExpression)
        || ts.isIdentifier(node) && forbidden.has(node.text)) throw Error('Forbidden dynamic execution or platform access in ' + name + '.ets');
    ts.forEachChild(node, visit);
  })(source);
}
