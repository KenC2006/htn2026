// Parse candidates before DevEco executes build tooling. This is a scope policy, not an OS sandbox.
const fs=require('fs');
const path=require('path');
const ts=require(process.argv[2]);
const root=process.argv[3];
const forbidden=new Set(['eval','Function','require','globalThis','console','hilog','Reflect','Proxy','Date','setTimeout','setInterval','fetch']);
for(const name of ['BucketStart','ClampValue','SummarizeBuckets']) {
  const source=ts.createSourceFile(name+'.ts',fs.readFileSync(path.join(root,name+'.ets'),'utf8'),ts.ScriptTarget.Latest,true);
  if(source.parseDiagnostics.length) throw Error('Candidate parse failed');
  for(const statement of source.statements) {
    if(ts.isImportDeclaration(statement)) {
      if(name!=='SummarizeBuckets' || statement.moduleSpecifier.text!=='./BucketStart' || !statement.importClause || statement.importClause.name || !statement.importClause.namedBindings || !ts.isNamedImports(statement.importClause.namedBindings) || statement.importClause.namedBindings.elements.length!==1 || statement.importClause.namedBindings.elements[0].name.text!=='bucketStart' || statement.importClause.namedBindings.elements[0].propertyName) throw Error('Unapproved candidate import');
    } else if(!ts.isFunctionDeclaration(statement)) throw Error('Only function declarations and approved imports are permitted');
  }
  function visit(node) {
    if(node.kind===ts.SyntaxKind.ImportKeyword || ts.isExportDeclaration(node) || ts.isElementAccessExpression(node) && ts.isStringLiteral(node.argumentExpression) || ts.isIdentifier(node) && forbidden.has(node.text)) throw Error('Forbidden dynamic execution or platform access');
    ts.forEachChild(node,visit);
  }
  visit(source);
}
