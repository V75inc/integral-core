import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import path from 'node:path';

/** Keep require('react') on the host's exact React object. The pinned Rolldown
 * external-require plugin copies the ESM namespace, losing React internals. */
export function sharedReactExternal() {
  const virtual = '\0integral-shared-react-cjs';
  return {
    name: 'integral-shared-react', enforce: 'pre',
    resolveId: {order: 'pre', handler(source, _importer, options) {
      if (source === 'react') return options.kind === 'require-call' ? virtual : {id: 'react', external: true};
    }},
    load(id) { if (id === virtual) return "import React from 'react'; module.exports = React;"; },
  };
}

/** Vite library configuration for an independently shipped App component. */
export async function appUIBuild({entry, outDir, resolveFrom = process.cwd()}) {
  const require = createRequire(path.resolve(resolveFrom, "package.json"));
  const viteRequire = createRequire(require.resolve("vite/package.json"));
  const {esmExternalRequirePlugin} = await import(pathToFileURL(viteRequire.resolve("rolldown/plugins")).href);
  const allowed = new Set(['react', 'react/jsx-runtime', '@integral/sdk-react']);
  return {
    configFile: false,
    define: {"process.env.NODE_ENV": JSON.stringify("production")},
    build: {
      outDir, emptyOutDir: false, minify: true,
      lib: {entry, formats: ['es'], fileName: () => 'component.mjs'},
      rolldownOptions: {plugins: [esmExternalRequirePlugin({external: [...allowed].filter(name => name !== 'react')})], output: {codeSplitting: false}},
    },
    plugins: [sharedReactExternal(), {
      name: 'integral-ui-contract',
      generateBundle(_options, bundle) {
        const files = Object.values(bundle);
        if (files.length !== 1 || files[0].type !== 'chunk') throw new Error('App UI must be one self-contained ES module; use shared tokens or namespaced inline styles.');
        if (Object.keys(files[0].modules).some(id => /[/\\]node_modules[/\\]react[/\\]/.test(id))) throw new Error('React must be provided by the Integral runtime, not bundled.');
        const ast = this.parse(files[0].code);
        const visit = node => {
          if (!node || typeof node !== 'object') return;
          if (node.type === 'ImportExpression') throw new Error('Dynamic imports are not supported in App UI v1.');
          if (['ImportDeclaration', 'ExportNamedDeclaration', 'ExportAllDeclaration'].includes(node.type) && node.source && !allowed.has(node.source.value)) throw new Error('Only Integral host runtime imports are supported.');
          for (const value of Object.values(node)) if (Array.isArray(value)) value.forEach(visit); else if (value && typeof value === 'object') visit(value);
        };
        visit(ast);
      },
    }],
  };
}
