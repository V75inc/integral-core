import { useEffect } from 'react';
import { useAuth } from '../../context/AuthContext';
import { initContentProfilePlugins } from '../../views/plugins/auto';

/** Load content-profile substrate catalogue only after authentication.

    ``initContentProfilePlugins`` hits ``GET /content-profile-substrate``
    (auth-required). Firing it at ``main.tsx`` boot caused a 401 on public
    surfaces (e.g. ``/invitations/:token`` in incognito), and the axios
    interceptor hard-redirected to ``/login`` before the invite page rendered.
*/
export function AuthBootstrappedPlugins() {
  const { user, loading } = useAuth();

  useEffect(() => {
    if (loading || !user) return;
    void initContentProfilePlugins();
  }, [user, loading]);

  return null;
}
