import { createRoot } from 'react-dom/client';

import { PublicWebChatPage } from './PublicWebChatPage';
import '../../index.css';
import '../../vendor/jvmessenger/thread.css';

const container = document.getElementById('public-web-chat-root');
if (container) {
  // An authorized HTTPS host projects these attributes into the escaped
  // template. An unconfigured template makes no chat request; the browser
  // never infers the route from a URL, storage, or a workspace session.
  createRoot(container).render(<PublicWebChatPage config={{
    route: container.dataset.webChatRoute,
    title: container.dataset.webChatTitle,
  }} />);
}
