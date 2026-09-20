// Runs the ORIGINAL TypeScript on the given cases. Generic: runners/functions.json says which file and which inputs, in order.
// usage: node source_runner.cjs <typescript.js> <legacy dir> <staging dir> <cases.jsonl> <out.jsonl>
const fs = require('fs');
const path = require('path');
const [compiler, source, outDir, casesPath, output] = process.argv.slice(2);
const ts = require(compiler);
const spec = JSON.parse(fs.readFileSync(path.join(__dirname, 'functions.json'), 'utf8'));
const js = ts.transpileModule(fs.readFileSync(path.join(source, spec.file), 'utf8'),
                              {compilerOptions: {target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS}}).outputText;
fs.writeFileSync(path.join(outDir, 'original.js'), js);
const original = require(path.join(outDir, 'original.js'));
const rows = fs.readFileSync(casesPath, 'utf8').trim().split(/\r?\n/).map(JSON.parse).map(c => {
  const f = spec.functions[c.export];
  if (!f) return {case_id: c.case_id, status: 'crash', diagnostics: 'unknown export ' + c.export};
  try {
    const value = original[c.export](...f.params.map(p => c.input[p]));
    return {case_id: c.case_id, status: 'ok', value: value === undefined ? null : value};
  } catch (e) {
    return {case_id: c.case_id, status: 'error', error_code: e instanceof Error ? e.message : String(e)};
  }
});
fs.writeFileSync(output, rows.map(x => JSON.stringify(x)).join('\n') + '\n');
