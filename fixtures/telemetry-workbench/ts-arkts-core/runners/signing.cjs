const fs=require('fs');
const path=require('path');
const home=process.argv[2];
const JSON5=require(path.join(home,'tools/hvigor/hvigor-ohos-plugin/node_modules/json5'));
const p=JSON5.parse(fs.readFileSync(process.argv[3],'utf8'));
const product=p.app.products.find(x=>x.name==='default');
const signing=p.app.signingConfigs.find(x=>x.name===product.signingConfig);
if(!signing) throw Error('No signing configuration selected for default product; apply signing in DevEco');
for(const key of ['certpath','profile','storeFile']) {
  const value=signing.material[key];
  if(!value || !path.isAbsolute(value) || !fs.existsSync(value)) throw Error('Signing material must reference existing absolute paths; configure in DevEco');
}
