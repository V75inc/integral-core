import { beforeEach, describe, expect, it } from 'vitest';
import { registerFieldType, resolveFieldType, _resetFieldTypeRegistryForTests } from './registry';
beforeEach(_resetFieldTypeRegistryForTests);
describe('explicit App renderer override', () => {
  it('preserves relation storage and declared scoping while choosing an installed renderer', () => {
    const editor = () => null;
    registerFieldType({type:'scoped_picker',editor,meta:{label:'Scoped',description:'App-owned picker'}});
    const field = {key:'agreement',name:'Agreement',type:'relation',config:{renderer:'scoped_picker',module:'custom_app'},relation:{target_app:'documents',many:false}};
    expect(resolveFieldType(field)?.editor).toBe(editor);
    expect(field.type).toBe('relation');
    expect(field.relation.target_app).toBe('documents');
  });
  it('does not silently fall back to broader choices if the configured renderer is absent', () => {
    expect(()=>resolveFieldType({key:'item',name:'Item',type:'relation',config:{renderer:'missing'}})).toThrow('Configured field renderer unavailable');
    expect(resolveFieldType({key:'item',name:'Item',type:'relation'})).toBeNull();
  });
});
