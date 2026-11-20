import { describe, expect, it } from 'vitest';
import { formConfirmation } from './formConfirmation';
describe('form confirmation presentation', () => {
  it('retains the default public and assigned experience', () => {
    expect(formConfirmation(undefined, false)).toEqual({title:'Response Recorded', message:'Thank you! Your response has been submitted successfully to the track.', allowAnotherResponse:true});
    expect(formConfirmation(null, true).message).toBe('Thank you! Your form has been submitted.');
  });
  it('allows explicit plain text and hides another-response only on boolean false', () => {
    expect(formConfirmation({title:'Application received',message:'We will contact you.',allow_another_response:false},false)).toEqual({title:'Application received',message:'We will contact you.',allowAnotherResponse:false});
    expect(formConfirmation({allow_another_response:'false'},false).allowAnotherResponse).toBe(true);
  });
});
