"""Offline qualification: actual catch bytes + real Playwright assertion failure.
No browser, Electron, provider, backend or operator state is exposed.
PLAYWRIGHT_MODULES must point to an already installed, trusted node_modules.
"""
import io,json,os,pathlib,subprocess,tarfile
root=pathlib.Path(__file__).resolve().parents[2]
modules=pathlib.Path(os.environ['PLAYWRIGHT_MODULES'])
version=json.loads((modules/'@playwright/test/package.json').read_text(encoding='utf-8'))['version']
if version != '1.62.1':
 raise SystemExit(f'Expected locked Playwright 1.62.1, got {version}')
print(f'Playwright version: {version}', flush=True)
source=(root/'apps/desktop/e2e/core/transcript-integrity.spec.ts').read_text(encoding='utf-8')
catch=source[source.index('  } catch (error) {'):source.index('    for (const proxy of proxies)')]
spec="""const { test, expect } = require('@playwright/test');
test('harmless failing assertion retains metadata', async ({}, testInfo) => {
 const ws={diagnostics:[{kind:'socket-created',socket:0}]};
 const app={close:async()=>{}};
 const page={evaluate:async()=>[]};
 try { expect('actual').toBe('expected');
"""+catch+"\n }\n});\n"
b=io.BytesIO()
with tarfile.open(fileobj=b,mode='w',dereference=True) as t:
 for p,n in [(os.environ.get('NODE_BINARY','/home/ubuntu/.local/bin/node'),'node')]+[(modules/x,'node_modules/'+x) for x in ['@playwright/test','playwright','playwright-core']]:t.add(p,arcname=n)
 for n,text in [('failure.spec.cjs',spec),('playwright.config.cjs',"module.exports={testDir:'.',testMatch:'failure.spec.cjs',retries:0,workers:1,reporter:[['json',{outputFile:'report.json'}]]};")]:
  data=text.encode();info=tarfile.TarInfo(n);info.size=len(data);t.addfile(info,io.BytesIO(data))
 command="""tar --no-same-owner -xf - && chmod +x node && ./node node_modules/playwright/cli.js test; rc=$?; ./node -e 'const fs=require("fs");const r=JSON.parse(fs.readFileSync("report.json"));const t=r.suites[0].specs[0].tests[0];const a=t.results[0].attachments; if(t.results[0].status!=="failed" || !a.some(x=>x.name==="reconnect-rpc-metadata"))process.exit(2); const x=a.find(x=>x.name==="reconnect-rpc-metadata"); const body=x.body?Buffer.from(x.body,"base64"):fs.readFileSync(x.path); if(JSON.parse(body)[0].kind!=="socket-created")process.exit(3);console.log(JSON.stringify({status:t.results[0].status,attachment:x.name,assertion:t.results[0].errors[0].message.includes("expected"),retries:t.retries}));'; verify=$?; test "$rc" = 1 && test "$verify" = 0"""
r=subprocess.run(['sudo','-n','bwrap','--die-with-parent','--unshare-all','--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/home','--setenv','HOME','/home','--chdir','/tmp','/usr/bin/sh','-c',command],input=b.getvalue(),capture_output=True,check=False)
print(r.stdout.decode(),r.stderr.decode());raise SystemExit(r.returncode)
