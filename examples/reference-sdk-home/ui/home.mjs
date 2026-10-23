import { defineHome, defineComponent } from '@integral/sdk-ui';
const Introduction = defineComponent(({ heading }) => ({
  id: 'introduction', type: 'stack', children: [
    { id: 'heading', type: 'heading', text: heading },
    { id: 'description', type: 'text', text: 'An App Home rendered directly by Integral.' },
  ],
}));
export default defineHome({ title: 'Home', renderer: 'native', content: {
  id: 'home', type: 'stack', children: [Introduction({ heading: 'Your work' }), {
    id: 'recent', type: 'records', title: 'Recent notes', query_key: 'notes',
  }],
} });
// Serialize this Home into app.home or submit it through the public Home API.
