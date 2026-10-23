import ts from 'typescript';
import { readFile, writeFile } from 'node:fs/promises';
const source = (await readFile(new URL('../src/index.ts', import.meta.url), 'utf8')).split('\n').filter(line => !line.includes("from './native.js'")).join('\n');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS } });
await writeFile(new URL('../dist/integral-sdk.js', import.meta.url), `(function(){const exports={};\n${outputText}\nglobalThis.IntegralSDK=Object.freeze(exports);})();\n`);
