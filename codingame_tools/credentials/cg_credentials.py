"""Management of cached persistent credentials for the CodinGame client."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Final

from private_files import PrivateDirManager, PrivateFilesManager, get_private_files
from typing_extensions import override

from ..common.dataclass_wizard_x import CatchAll, JSONWizardX
from ..common.typedefs import CLIENT_APP_NAME, DEFAULT_PROFILE_NAME, PROFILES_SUBDIR

__all__ = [
    # Constants
    "REMEMBER_ME_TOKEN_ENV_VAR",
    "CG_SESSION_TOKEN_ENV_VAR",
    "CREDENTIALS_FILENAME",
    "CLIENT_APP_NAME",
    "DEFAULT_PROFILE_NAME",
    "PROFILES_SUBDIR",
    # Profile name validation
    "is_valid_profile_name",
    "validate_profile_name",
    # Credentials data
    "CgCredentials",
    # Single-profile storers
    "CgCredentialsStorer",
    "CgInMemoryCredentialsStorer",
    "CgPrivateFileCredentialsStorer",
    # Multi-profile storers
    "CgCredentialsProfileStorer",
    "CgInMemoryCredentialsProfileStorer",
    "CgPrivateFileCredentialsProfileStorer",
    # Caching stores built on top of the storers
    "CgCredentialsStore",
    "CgCredentialsProfileStore",
    "get_credentials_store",
    "get_in_memory_credentials_store",
    # Simplified module-level convenience functions
    "get_credentials",
    "set_credentials",
    "get_credentials_with_override",
]

REMEMBER_ME_TOKEN_ENV_VAR: Final[str] = "CODINGAME_REMEMBER_ME"
"""The name of the environment variable that can be set to provide the CodinGame remember_me cookie for authentication."""

CG_SESSION_TOKEN_ENV_VAR: Final[str] = "CODINGAME_SESSION"
"""The name of the environment variable that can be set to provide the CodinGame cg_session cookie for authentication."""

CREDENTIALS_FILENAME: Final[str] = "credentials.json"
"""Name of the JSON file, within a per-app private directory, that persisted credentials are stored in."""

def is_valid_profile_name(profile_name: str) -> bool:
    """Return True if the given profile name is valid, False otherwise. Profile names must be valid python
       identifiers and must not start with an underscore.  They are case-sensitive"""
    return profile_name.isidentifier() and not profile_name.startswith("_")

def validate_profile_name(profile_name: str) -> None:
    """Raise a ValueError if the given profile name is invalid. Profile names must be valid python
       identifiers and must not start with an underscore.  They are case-sensitive"""
    if not is_valid_profile_name(profile_name):
        raise ValueError(
                f"Invalid profile name: {profile_name!r}. Profile names must be valid "
                "python identifiers and must not start with an underscore."
            )

@dataclass
class CgCredentials(JSONWizardX):
    """Persistable CodinGame session credentials.

       Both cookies are optional since a partially-completed browser login may capture
       only the `rememberMe` cookie before the `cgSession` cookie becomes available.
    """

    # `extra_data` is deliberately the first field with a default: dataclass_wizard 1.0.0 mis-binds
    # any defaulted field positioned immediately before it (silently, no error) to the CatchAll's
    # own value. Keeping it first among the defaulted fields makes that impossible. There are no
    # required fields in this class, so it ends up first overall.
    extra_data: CatchAll = field(default_factory=dict)
    """Unrecognized fields encountered when loading a credentials file, preserved so that
       round-tripping through `saves()`/`loads()` does not silently drop data."""

    remember_me_cookie: str | None = None
    """Value of the CodinGame `rememberMe` cookie, used to establish a new session."""

    cg_session_cookie: str | None = None
    """Value of the CodinGame `cgSession` cookie for an active session. Required for some
       operations (e.g., file upload) that are not supported via `rememberMe` alone."""
       
class CgCredentialsStorer(ABC):
    """Abstract base class for a storer of a single CgCredentials instance."""
    
    def __init__(self) -> None:
        super().__init__()

    @abstractmethod
    def read_persistent_credentials(self) -> CgCredentials:
        """Read the credentials from persistent storage. Subclasses implement this method to provide the actual storage mechanism."""
        raise NotImplementedError()

    @abstractmethod
    def write_persistent_credentials(self, credentials: CgCredentials) -> None:
        """Write the credentials to persistent storage. Subclasses implement this method to provide the actual storage mechanism."""
        raise NotImplementedError()
    
    @abstractmethod
    def persistent_credentials_exist(self) -> bool:
        """Return True if the credentials exist in persistent storage, False otherwise. Subclasses implement this method to
           provide the actual storage mechanism."""
        raise NotImplementedError()
    
    @abstractmethod
    def delete_persistent_credentials(self) -> None:
        """Delete the credentials from persistent storage. Subclasses implement this method to provide the actual storage mechanism."""
        raise NotImplementedError()
        
class CgInMemoryCredentialsStorer(CgCredentialsStorer):
    """A CgCredentialsStorer that stores credentials in memory only, without any persistent storage."""
    
    _credentials: CgCredentials | None
    """The in-memory credentials. If None, no credentials are stored."""
    
    def __init__(self) -> None:
        super().__init__()
        self._credentials = None

    @override
    def read_persistent_credentials(self) -> CgCredentials:
        """Return the stored credentials, or an empty CgCredentials instance if none are stored."""
        return deepcopy(self._credentials) if self._credentials is not None else CgCredentials()

    @override
    def write_persistent_credentials(self, credentials: CgCredentials) -> None:
        """Store the given credentials in memory."""
        self._credentials = deepcopy(credentials)

    @override
    def persistent_credentials_exist(self) -> bool:
        """Return True if credentials are stored in memory, False otherwise."""
        return self._credentials is not None
    
    @override
    def delete_persistent_credentials(self) -> None:
        """Delete the stored credentials from memory."""
        self._credentials = None
    
class CgPrivateFileCredentialsStorer(CgCredentialsStorer):
    """A CgCredentialsStorer that stores credentials in a protected private file."""
    
    _private_dir_manager: PrivateDirManager    
    _file_path: Path

    def __init__(self, private_dir_manager: PrivateDirManager, file_path: Path | str) -> None:
        super().__init__()
        self._private_dir_manager = private_dir_manager
        self._file_path = self._private_dir_manager.get_private_file(file_path, create_parent=True)

    @override
    def read_persistent_credentials(self) -> CgCredentials:
        """Read the credentials from the per-app private file."""
        if not self.persistent_credentials_exist():
            return CgCredentials()  # If the file does not exist, return empty credentials
        with self._private_dir_manager.open(self._file_path, "r") as f:
            credentials_json = f.read()
            credentials: CgCredentials = CgCredentials.loads(credentials_json)
            return credentials

    @override
    def write_persistent_credentials(self, credentials: CgCredentials) -> None:
        """Write the credentials to the private file."""
        with self._private_dir_manager.open(self._file_path, "w", atomic_update=True, create_parent=True) as f:
            f.write(credentials.saves())

    def persistent_credentials_exist(self) -> bool:
        """Return True if the credentials file exists in the private directory, False otherwise."""
        return self._file_path.exists()

    @override
    def delete_persistent_credentials(self) -> None:
        """Delete the stored credentials from the private file."""
        if self._file_path.exists():
            self._file_path.unlink()
    
class CgCredentialsProfileStorer(ABC):
    """Abstract base class for a storer of multiple CgCredentials instances, keyed by profile name."""

    @abstractmethod
    def create_single_profile_storer(self, profile_name: str) -> CgCredentialsStorer:
        """Create a CgCredentialsStorer for the given profile name. Subclasses implement this method to provide
           the actual storage mechanism."""
        raise NotImplementedError()
    
    @abstractmethod
    def list_persistent_profile_names(self) -> list[str]:
        """Return a list of all persistent profile names. Subclasses implement this method
           to provide the actual storage mechanism."""
        raise NotImplementedError()
    
class CgInMemoryCredentialsProfileStorer(CgCredentialsProfileStorer):
    """A CgCredentialsProfileStorer that stores credentials in memory only, without any persistent storage."""
    
    _profiles: dict[str, CgInMemoryCredentialsStorer]
    """An in-memory dictionary of stored profiles."""
    
    def __init__(self) -> None:
        super().__init__()
        self._profiles = {}

    @override
    def create_single_profile_storer(self, profile_name: str) -> CgCredentialsStorer:
        """Create a CgInMemoryCredentialsStorer for the given profile name."""
        validate_profile_name(profile_name)
        storer = self._profiles.get(profile_name)
        if storer is None:
            storer = CgInMemoryCredentialsStorer()
            self._profiles[profile_name] = storer
        return storer

    @override
    def list_persistent_profile_names(self) -> list[str]:
        """Return a list of all profile names that have stored credentials in memory."""
        profile_names: list[str] = []
        for profile_name, storer in self._profiles.items():
            if storer.persistent_credentials_exist():
                profile_names.append(profile_name)
        profile_names.sort()
        return profile_names

class CgPrivateFileCredentialsProfileStorer(CgCredentialsProfileStorer):
    """A CgCredentialsProfileStorer that stores credentials in a private file under a per-profile directory."""
    
    _private_dir_manager: PrivateDirManager
    """The PrivateDirManager for the parent directory of all profile directories."""
    
    _profiles_dir: Path
    """The path to the directory containing all profile directories."""
    
    def __init__(self, private_dir_manager: PrivateDirManager) -> None:
        super().__init__()
        self._private_dir_manager = private_dir_manager
        self._profiles_dir = self._private_dir_manager.get_private_dir(PROFILES_SUBDIR)

    def credentials_file(self, profile_name: str) -> Path:
        """Return the path to the credentials file for the given profile name."""
        return self._private_dir_manager.get_private_file(
                self._profiles_dir / profile_name / "credentials.json", create_parent=True
            )
    
    @override
    def create_single_profile_storer(self, profile_name: str) -> CgCredentialsStorer:
        """Create a CgInMemoryCredentialsStorer for the given profile name."""
        validate_profile_name(profile_name)
        storer = CgPrivateFileCredentialsStorer(self._private_dir_manager, self.credentials_file(profile_name))
        return storer

    @override
    def list_persistent_profile_names(self) -> list[str]:
        """Return a list of all profile names that have credentials.json files."""
        result: list[str] = []
        if self._profiles_dir.exists():
            for d in self._profiles_dir.iterdir():
                if d.is_dir():
                    profile_name = d.name
                    if is_valid_profile_name(profile_name):
                        cred_file = d / "credentials.json"
                        if cred_file.exists():
                            result.append(profile_name)
        result.sort()
        return result
    
class CgCredentialsStore:
    """A store of a single CgCredentials instance, with a backing Storer implementation."""
    
    _storer: CgCredentialsStorer
    """The storer that provides the persistent storage mechanism for the credentials."""
    
    _cache: CgCredentials | None = None
    """The in-memory cache of the credentials. If None and _cache_fresh,
       then it represents a deleted or nonexistent credentiasls.."""
    
    _cache_fresh: bool = False
    """Indicates whether the in-memory cache has been set, either with persistent credentials or
       via set_credentials()."""
    
    _persistent_credentials: CgCredentials | None = None
    """The last known persistent credentials, used to detect changes and avoid unnecessary writes. If
       None and _persistent_fresh, then persistent credentials do not exist."""
       
    _presistent_fresh: bool = False
    """Indicates whether _persistent_credentials is up to date."""
    
    def __init__(self, storer: CgCredentialsStorer) -> None:
        self._storer = storer
        
    @property
    def dirty(self) -> bool:
        """Indicates whether the in-memory cache has uncommitted changes."""
        return self._cache_fresh and (not self._presistent_fresh or self._cache != self._persistent_credentials)
    
    def fetch(self, force: bool = False) -> None:
        """Read from from persistent storage if necessary."""
        if force or not self._presistent_fresh:
            if not self._storer.persistent_credentials_exist():
                self._persistent_credentials = None
            else:
                self._persistent_credentials = deepcopy(self._storer.read_persistent_credentials())
            self._presistent_fresh = True
            if not self._cache_fresh:
                self._cache = deepcopy(self._persistent_credentials)
                self._cache_fresh = True
            
    def commit(self, force: bool=False) -> None:
        """Commit changes to persistent storage."""
        if not self._cache_fresh:
            return # nothing has been written
        need_update = force or not self._presistent_fresh or self._cache != self._persistent_credentials
        if need_update:
            if self._cache is None:
                self._storer.delete_persistent_credentials()
                self._persistent_credentials = None
            else:
                self._storer.write_persistent_credentials(self._cache)
                self._persistent_credentials = deepcopy(self._cache)
            self._presistent_fresh = True
            
    def cancel(self) -> None:
        """Discard any uncommitted changes and reset the in-memory cache to the last known persistent credentials."""
        self._cache = None
        self._cache_fresh = False

    def set_credentials(self, credentials: CgCredentials | None) -> None:
        """Set the in-memory cache to the given credentials and mark it as dirty if changed. If
           None, the credentials become deleted/nonexistent. Will not write to persistent storage until `commit()` is called."""
        self._cache = deepcopy(credentials)
        self._cache_fresh = True
        
    def get_credentials(self) -> CgCredentials | None:
        """Return a deep copy of the current, possibly uncommited credentials. If no credentials
           exist, returns None."""
        if self._cache is None:
            self.fetch()
        return deepcopy(self._cache)

class CgCredentialsProfileStore:
    """A store of multiple CgCredentials instances, keyed by profile name, with a backing Storer implementation."""
    
    _profile_storer: CgCredentialsProfileStorer
    """The storer that provides the persistent storage mechanism for the credentials."""
    
    _profile_stores: dict[str, CgCredentialsStore]
    """A dictionary of profile names to CgCredentialsStore instances."""
    
    _profile_stores_fresh: bool = False
    """Indicates whether the _profile_stores dictionary has been populated with all known profiles from persistent storage."""
    
    def __init__(self, profile_storer: CgCredentialsProfileStorer) -> None:
        self._profile_storer = profile_storer
        self._profile_stores = {}
        
    def get_profile_store(self, profile_name: str) -> CgCredentialsStore:
        """Return a CgCredentialsStore for the given profile name, creating one if necessary."""
        validate_profile_name(profile_name)
        store = self._profile_stores.get(profile_name)
        if store is None:
            storer = self._profile_storer.create_single_profile_storer(profile_name)
            store = CgCredentialsStore(storer)
            self._profile_stores[profile_name] = store
        return store
    
    def __getitem__(self, profile_name: str) -> CgCredentialsStore:
        """Return a CgCredentialsStore for the given profile name, creating one if necessary."""
        return self.get_profile_store(profile_name)
    
    def freshen(self, force: bool = False) -> None:
        """Populate the _profile_stores dictionary with all known profiles from persistent storage.
           Note that profiles are never removed from the dictionary until prune_deleted_profiles() is called, even
           if they are deleted from persistent storage. Deleted profiles will be represented by a
           CgCredentialsStore with CgCredentials set to None."""
        if force or not self._profile_stores_fresh:
            profile_names = self._profile_storer.list_persistent_profile_names()
            for profile_name in profile_names:
                self.get_profile_store(profile_name)
            self._profile_stores_fresh = True
            
    def list_profile_names(self) -> list[str]:
        """Return a list of all known profile names, including those that have been deleted from persistent storage
           since startup."""
        self.freshen()
        return sorted(self._profile_stores.keys())
    
    def commit(self, force: bool = False) -> None:
        """Commit changes to persistent storage for all profiles."""
        for store in self._profile_stores.values():
            store.commit(force=force)
            
    def cancel(self) -> None:
        """Discard any uncommitted changes for all profiles and reset the in-memory cache to the last known persistent credentials."""
        for store in self._profile_stores.values():
            store.cancel()
            
    def prune_deleted_profiles(self) -> None:
        """Remove any profiles from the _profile_stores dictionary that have no uncommitted changes
           and do not exist in persistent storage."""
        self.freshen()
        to_remove = []
        for profile_name, store in self._profile_stores.items():
            store.fetch()
            if not store.dirty and store.get_credentials() is None:
                to_remove.append(profile_name)
        for profile_name in to_remove:
            del self._profile_stores[profile_name]
            
    def set_credentials(self, profile_name: str, credentials: CgCredentials | None) -> None:
        """Set the in-memory cache for the given profile name to the given credentials and mark it as dirty if changed. If
           None, the credentials become deleted/nonexistent. Will not write to persistent storage until `commit()` is called."""
        store = self.get_profile_store(profile_name)
        store.set_credentials(credentials)
        
    def get_credentials(self, profile_name: str) -> CgCredentials | None:
        """Return a deep copy of the current, possibly uncommited credentials for the given profile name. If no credentials
           exist, returns None."""
        store = self.get_profile_store(profile_name)
        return store.get_credentials()

@cache
def _get_credentials_store(private_files_manager: PrivateFilesManager) -> CgCredentialsProfileStore:
    """Get a persistent, private CgCredentialsProfileStore singleton for the given app name, or the default app name if None."""
    return CgCredentialsProfileStore(CgPrivateFileCredentialsProfileStorer(private_files_manager))

def get_credentials_store(app_name: str | None = None) -> CgCredentialsProfileStore:
    """Get a persistent, private CgCredentialsProfileStore singleton for the given app name, or the default app name if None."""
    return _get_credentials_store(get_private_files(app_name=CLIENT_APP_NAME if app_name is None else app_name))

@cache
def _get_in_memory_credentials_store() -> CgCredentialsProfileStore:
    """Get a CgCredentialsProfileStore singleton for in-memory use. Not persistent."""
    return CgCredentialsProfileStore(CgInMemoryCredentialsProfileStorer())

def get_in_memory_credentials_store() -> CgCredentialsProfileStore:
    """Get a CgCredentialsProfileStore singleton for in-memory use. Not persistent."""
    return _get_in_memory_credentials_store()

def get_credentials(
            *,
            profile_name: str | None = None,
            store: CgCredentialsProfileStore | None = None,
            app_name: str | None = None
        ) -> CgCredentials:
    """Simplified function to get the current credentials for a profile, without any overrides.
       If no credentials are available, returns an empty `CgCredentials()` object.
       
       Args:
           profile_name: Optional profile name to use for fetching credentials. If None, the default profile is used.
           store:        Optional CgCredentialsProfileStore to use for fetching credentials.
                         If None, the default persistent store singleton for the given app name is used.
           app_name:     The app namespace to read credentials from when store is None; defaults to `CLIENT_APP_NAME`.

       Returns:
           Resolved `CgCredentials` object. If no credentials are available, returns an empty `CgCredentials()` object.
       """
    if store is None:
        store = get_credentials_store(app_name=app_name)
    if profile_name is None:
        profile_name = DEFAULT_PROFILE_NAME
    credentials = store.get_credentials(profile_name)
    if ( credentials is not None
            and credentials.remember_me_cookie is not None
            and credentials.cg_session_cookie is not None ):
        credentials = deepcopy(credentials)
    else:
        credentials = CgCredentials()
    return credentials

def set_credentials(
            credentials: CgCredentials | None,
            *,
            profile_name: str | None = None,
            store: CgCredentialsProfileStore | None = None,
            app_name: str | None = None
        ) -> None:
    """Simplified function to set the current credentials for a profile in a credential store and immediately commit.
       
       Args:
           credentials:  The CgCredentials object to set for the given profile. If None, the credentials become deleted/nonexistent. 
           profile_name: Optional profile name to use for setting credentials. If None, the default profile is used.
           store:        Optional CgCredentialsProfileStore in which to set credentials.
                         If None, the default persistent store singleton for the given app name is used.
           app_name:     The app namespace to use when store is None; defaults to `CLIENT_APP_NAME`.
       """
    if store is None:
        store = get_credentials_store(app_name=app_name)
    if profile_name is None:
        profile_name = DEFAULT_PROFILE_NAME
    store.set_credentials(profile_name, credentials)
    store.commit()  # Commit changes to persistent storage immediately

def get_credentials_with_override(
            *,
            profile_name: str | None = None,
            store: CgCredentialsProfileStore | None = None,
            credentials: CgCredentials | None = None,
            remember_me_token: str | None = None,
            cg_session_token: str | None = None,
            app_name: str | None = None
        ) -> CgCredentials:
    """Return the current credentials for an app, with environment variable overrides.

       Resolution order:
          1. If non-null `remember_me_token` / `cg_session_token` are provided, use those values. If one is
             provided, both must be provided.
          2. If `credentials` is provided with non-null tokens, use non-null token values from that object. Both must be non-null.
          3. check the `REMEMBER_ME_TOKEN_ENV_VAR` / `CG_SESSION_TOKEN_ENV_VAR` environment variables for overrides. If one
                is provided, both must be provided.
          4. If store is provided, check the store for the given profile name (or the default profile if None) and use those
             values if available.
          5. If store is None, use the default persistent store for the given app name (or the default app name if None)
             and check for the given profile name (or the default profile if None) and use those values if available.
          6. If none of the above are available, return an empty `CgCredentials()`
       
       Note that overrides, if any, are not persisted to store.

       Args:
           profile_name: Optional profile name to use for fetching credentials. If None, the default profile is used.
           store: Optional CgCredentialsProfileStore to use for fetching credentials.
               If None, the default persistent store singleton for the given app name is used.
           credentials: Optional CgCredentials object to use as an override. Ignored if None or if either of the cookie values are None.
           remember_me_token: Optional override for the `rememberMe` cookie value.
           cg_session_token: Optional override for the `cgSession` cookie value.
           app_name: The app namespace to read credentials from when store is None; defaults to `CLIENT_APP_NAME`.

       Returns:
           Resolved `CgCredentials` object, with parameter and environment variable overrides applied. If
           no credentials are available, returns an empty `CgCredentials()` object.
    """
    env_remember_me_token = os.getenv(REMEMBER_ME_TOKEN_ENV_VAR)
    if env_remember_me_token is not None:
        env_remember_me_token = env_remember_me_token.strip()
        if env_remember_me_token == "":
            env_remember_me_token = None  # Treat empty string as None
    env_cg_session_token = os.getenv(CG_SESSION_TOKEN_ENV_VAR)
    if env_cg_session_token is not None:
        env_cg_session_token = env_cg_session_token.strip()
        if env_cg_session_token == "":
            env_cg_session_token = None  # Treat empty string as None
    result = CgCredentials()
    if remember_me_token is not None and cg_session_token is not None:
        result = CgCredentials(remember_me_cookie=remember_me_token, cg_session_cookie=cg_session_token)
    elif credentials is not None and credentials.remember_me_cookie is not None and credentials.cg_session_cookie is not None:
        result = deepcopy(credentials)
    elif env_remember_me_token is not None and env_cg_session_token is not None:
        result = CgCredentials(remember_me_cookie=env_remember_me_token, cg_session_cookie=env_cg_session_token)
    else:
        stored_credentials = get_credentials(profile_name=profile_name, store=store, app_name=app_name)
        if ( stored_credentials is not None
                and stored_credentials.remember_me_cookie is not None
                and stored_credentials.cg_session_cookie is not None ):
            result = deepcopy(stored_credentials)
    return result