"""jvspatial Server extension selecting Integral's current-identity auth adapter."""

from jvspatial.api import Server
from jvspatial.api.components.auth_configurator import AuthConfigurator

from app.services.authentication import IntegralAuthenticationService


class IntegralAuthConfigurator(AuthConfigurator):
    """Bind the same hardened service to auth route closures and middleware."""

    def _register_auth_endpoints(self) -> None:
        self._auth_service = IntegralAuthenticationService(
            self._prime_graph_context,
            jwt_secret=self._auth_config.jwt_secret,
            jwt_algorithm=self._auth_config.jwt_algorithm,
            jwt_expire_minutes=self._auth_config.jwt_expire_minutes,
            refresh_expire_days=self._auth_config.refresh_expire_days,
            refresh_token_rotation=self._auth_config.refresh_token_rotation,
            blacklist_cache_ttl_seconds=self._auth_config.blacklist_cache_ttl_seconds,
            password_reset_token_expiry_minutes=(
                self._auth_config.password_reset_token_expiry_minutes
            ),
            role_permission_mapping=self._auth_config.role_permission_mapping,
            admin_role=self._auth_config.admin_role,
            default_role=self._auth_config.default_role,
            registration_open=self.config.auth.registration_open,
        )
        super()._register_auth_endpoints()


class IntegralServer(Server):
    """Keep the pinned Server lifecycle while substituting its auth configurator.

    jvspatial 0.1.1 has no authentication-service factory option. This one
    protected lifecycle override mirrors its configurator assignment seam;
    routes, OAuth, API keys and middleware remain library implementations.
    A compatibility test exercises actual route admission through this class.
    """

    def _configure_authentication(self) -> None:
        configurator = IntegralAuthConfigurator(self.config, self._logger, server=self)
        self._auth_config = configurator.configure()
        self._auth_router = configurator.auth_router
        self._auth_endpoints_registered = configurator.has_auth_endpoints
        self._has_auth_endpoints = configurator.has_auth_endpoints
        self._auth_service = configurator.auth_service
        self._oauth_router = configurator.oauth_router
        self._well_known_router = configurator.well_known_router
        if (
            self._auth_service
            and self.config.auth.bootstrap_admin_email
            and self.config.auth.bootstrap_admin_password
        ):
            self.lifecycle_manager.add_startup_hook(self._bootstrap_admin_startup)
