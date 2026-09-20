const fs = require('fs');
const path = require('path');
const [compiler, source, outDir, casesPath, output] = process.argv.slice(2);
const ts = require(compiler);
if (ts.version !== '4.9.5') throw Error('Expected pinned TypeScript 4.9.5');
const files = fs.readdirSync(source).filter(x => x.endsWith('.ts')).map(x => path.join(source,x));
const program = ts.createProgram(files,{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.CommonJS,strict:true,outDir});
const diagnostics = ts.getPreEmitDiagnostics(program);
if (diagnostics.length) throw Error(ts.formatDiagnosticsWithColorAndContext(diagnostics,{getCanonicalFileName:x=>x,getCurrentDirectory:()=>source,getNewLine:()=> '\n'}));
program.emit();
const {bucketStart} = require(path.join(outDir,'BucketStart.js'));
const {clampValue} = require(path.join(outDir,'ClampValue.js'));
const {summarizeBuckets} = require(path.join(outDir,'SummarizeBuckets.js'));
const rows=fs.readFileSync(casesPath,'utf8').trim().split(/\r?\n/).map(JSON.parse).map(c=> {
  const i=c.input;
  try {
    let value;
    if(c.chunk_id==='T1') value=bucketStart(i.timestampMs,i.widthMs);
    else if(c.chunk_id==='T2') value=clampValue(i.value,i.lower,i.upper);
    else if(c.chunk_id==='T3') value=summarizeBuckets(i.timestamps,i.widthMs);
    else throw Error('UNKNOWN_CHUNK');
    return {case_id:c.case_id,status:'ok',value};
  } catch(e) {
    if(!['INVALID_WIDTH','INVALID_BOUNDS'].includes(e.message)) throw e;
    return {case_id:c.case_id,status:'error',error_code:e.message};
  }
});
fs.writeFileSync(output,rows.map(x=>JSON.stringify(x)).join('\n')+'\n');
