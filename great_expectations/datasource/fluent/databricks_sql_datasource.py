from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar, List, Literal, Type, Union, overload
from urllib import parse

from great_expectations._docs_decorators import public_api
from great_expectations.compatibility import pydantic
from great_expectations.compatibility.pydantic import AnyUrl
from great_expectations.compatibility.sqlalchemy import (
    sqlalchemy as sa,
)
from great_expectations.compatibility.typing_extensions import override
from great_expectations.datasource.fluent.config_str import ConfigStr
from great_expectations.datasource.fluent.interfaces import (
    DataAsset,
    TestConnectionError,
)
from great_expectations.datasource.fluent.sql_datasource import (
    QueryAsset as SqlQueryAsset,
)
from great_expectations.datasource.fluent.sql_datasource import (
    SQLDatasource,
)
from great_expectations.datasource.fluent.sql_datasource import (
    TableAsset as SqlTableAsset,
)

if TYPE_CHECKING:
    from sqlalchemy.sql import quoted_name  # noqa: TID251 # type-checking only

    from great_expectations.compatibility import sqlalchemy
    from great_expectations.compatibility.pydantic.networks import Parts
    from great_expectations.core.config_provider import _ConfigurationProvider


def _parse_param_from_query_string(param: str, query: str) -> str | None:
    url_components = parse.urlparse(query)
    path = str(url_components.path)
    parse_results: dict[str, list[str]] = parse.parse_qs(path)
    path_results = parse_results.get(param, [])

    if not path_results:
        return None
    if len(path_results) > 1:
        raise ValueError(f"Only one `{param}` query entry is allowed")  # noqa: TRY003 # FIXME CoP
    return path_results[0]


class _UrlQueryError(pydantic.UrlError):
    """
    Custom Pydantic error for missing query in DatabricksDsn.
    """

    code = "url.query"
    msg_template = "URL query is invalid or missing"


class _UrlHttpPathError(pydantic.UrlError):
    """
    Custom Pydantic error for missing http_path in DatabricksDsn query.
    """

    code = "url.query.http_path"
    msg_template = "'http_path' query param is invalid or missing"


class _UrlCatalogError(pydantic.UrlError):
    """
    Custom Pydantic error for missing catalog in DatabricksDsn query.
    """

    code = "url.query.catalog"
    msg_template = "'catalog' query param is invalid or missing"


class _UrlSchemaError(pydantic.UrlError):
    """
    Custom Pydantic error for missing schema in DatabricksDsn query.
    """

    code = "url.query.schema"
    msg_template = "'schema' query param is invalid or missing"


class DatabricksDsn(AnyUrl):
    allowed_schemes = {
        "databricks",
    }
    query: str  # if query is not provided, validate_parts() will raise an error

    @classmethod
    @override
    def validate_parts(cls, parts: Parts, validate_port: bool = True) -> Parts:
        """
        Overridden to validate additional fields outside of scheme (which is performed by AnyUrl).
        """
        query = parts["query"]
        if query is None:
            raise _UrlQueryError()

        http_path = _parse_param_from_query_string(param="http_path", query=query)
        if http_path is None:
            raise _UrlHttpPathError()

        catalog = _parse_param_from_query_string(param="catalog", query=query)
        if catalog is None:
            raise _UrlCatalogError()

        schema = _parse_param_from_query_string(param="schema", query=query)
        if schema is None:
            raise _UrlSchemaError()

        return AnyUrl.validate_parts(parts=parts, validate_port=validate_port)

    @overload
    @classmethod
    def parse_url(
        cls, url: ConfigStr, config_provider: _ConfigurationProvider = ...
    ) -> DatabricksDsn: ...

    @overload
    @classmethod
    def parse_url(
        cls, url: str, config_provider: _ConfigurationProvider | None = ...
    ) -> DatabricksDsn: ...

    @classmethod
    def parse_url(
        cls, url: ConfigStr | str, config_provider: _ConfigurationProvider | None = None
    ) -> DatabricksDsn:
        if isinstance(url, ConfigStr):
            assert config_provider, "`config_provider` must be provided"
            url = url.get_config_value(config_provider=config_provider)
        parsed_url = pydantic.parse_obj_as(DatabricksDsn, url)
        return parsed_url


class DatabricksTableAsset(SqlTableAsset):
    @pydantic.validator("table_name")
    @override
    def _resolve_quoted_name(cls, table_name: str) -> str | quoted_name:
        """Resolve quoted names and handle Databricks backtick notation."""
        from great_expectations.compatibility import sqlalchemy

        # If it's already a quoted_name, return as-is
        if sqlalchemy.quoted_name and isinstance(table_name, sqlalchemy.quoted_name):
            return table_name

        # Check if the table name is quoted/bracketed (including Databricks backticks)
        table_name_is_quoted = cls._is_bracketed_by_quotes(table_name)

        if sqlalchemy.quoted_name:  # type: ignore[truthy-function]
            if table_name_is_quoted:
                # Handle different quote types - strip them and mark as quoted
                clean_table_name = table_name.strip('"').strip("'").strip("`")
                return sqlalchemy.quoted_name(value=clean_table_name, quote=True)

            # Check if Databricks backticks are needed based on content
            if cls._needs_databricks_backticks(table_name):
                return sqlalchemy.quoted_name(value=table_name, quote=True)

        return table_name

    @pydantic.validator("schema_name", pre=True)
    def _resolve_schema_quoted_name(cls, schema_name: str | None) -> str | quoted_name | None:
        """Resolve quoted names for schema and handle Databricks backtick notation."""
        if schema_name is None:
            return None

        from great_expectations.compatibility import sqlalchemy

        # If it's already a quoted_name, return as-is
        if sqlalchemy.quoted_name and isinstance(schema_name, sqlalchemy.quoted_name):
            return schema_name

        # Check if the schema name is quoted/bracketed
        schema_name_is_quoted = cls._is_bracketed_by_quotes(schema_name)

        if sqlalchemy.quoted_name:  # type: ignore[truthy-function]
            if schema_name_is_quoted:
                # Handle different quote types - strip them and mark as quoted
                clean_schema_name = schema_name.strip('"').strip("'").strip("`")
                return sqlalchemy.quoted_name(value=clean_schema_name, quote=True)

            # Check if Databricks backticks are needed based on content
            if cls._needs_databricks_backticks(schema_name):
                return sqlalchemy.quoted_name(value=schema_name, quote=True)

        return schema_name

    @staticmethod
    def _needs_databricks_backticks(name: str) -> bool:
        """
        Returns True if the name requires backticks in Databricks.

        Databricks requires backticks for identifiers that:
        - Start with a number
        - Contain spaces, hyphens, dots, or other special characters
        - Are reserved keywords
        """
        import re

        # Check if name starts with a number
        if re.match(r"^\d", name):
            return True

        # Check if name contains special characters that need escaping
        if re.search(r"[.\s\-#@]", name):
            return True

        return False

    @staticmethod
    @override
    def _is_bracketed_by_quotes(target: str) -> bool:
        """
        Returns True if the target string is bracketed by quotes.

        Supports standard quotes ('', "") and Databricks backticks (`).

        Arguments:
            target: A string to check if it is bracketed by quotes.

        Returns:
            True if the target string is bracketed by quotes.
        """
        # Check standard quotes
        from great_expectations.datasource.fluent.sql_datasource import DEFAULT_QUOTE_CHARACTERS
        for quote in DEFAULT_QUOTE_CHARACTERS:
            if target.startswith(quote) and target.endswith(quote):
                return True

        # Check Databricks backticks
        if target.startswith("`") and target.endswith("`"):
            return True

        return False


@public_api
class DatabricksSQLDatasource(SQLDatasource):
    """Adds a DatabricksSQLDatasource to the data context.

    Args:
        name: The name of this DatabricksSQL datasource.
        connection_string: The SQLAlchemy connection string used to connect to the Databricks SQL database.
            For example: "databricks://token:<token>@<host>:<port>?http_path=<http_path>&catalog=<catalog>&schema=<schema>""
        assets: An optional dictionary whose keys are TableAsset or QueryAsset names and whose values
            are TableAsset or QueryAsset objects.
    """  # noqa: E501 # FIXME CoP

    # class var definitions
    asset_types: ClassVar[List[Type[DataAsset]]] = [DatabricksTableAsset, SqlQueryAsset]

    type: Literal["databricks_sql"] = "databricks_sql"  # type: ignore[assignment] # FIXME CoP
    connection_string: Union[ConfigStr, DatabricksDsn]

    # These are instance var because ClassVars can't contain Type variables. See
    # https://peps.python.org/pep-0526/#class-and-instance-variable-annotations
    _TableAsset: Type[SqlTableAsset] = pydantic.PrivateAttr(DatabricksTableAsset)


    @override
    def test_connection(self, test_assets: bool = True) -> None:
        try:
            super().test_connection(test_assets)
        except TestConnectionError as e:
            nested_exception = None
            if e.__cause__ and e.__cause__.__cause__:
                nested_exception = e.__cause__.__cause__

            # Raise specific error informing how to install dependencies only if relevant
            if isinstance(nested_exception, sa.exc.NoSuchModuleError):
                raise TestConnectionError(  # noqa: TRY003 # FIXME CoP
                    "Could not connect to Databricks - please ensure you've installed necessary dependencies with `pip install great_expectations[databricks]`."  # noqa: E501 # FIXME CoP
                ) from e
            raise e  # noqa: TRY201 # FIXME CoP

    @override
    def _create_engine(self) -> sqlalchemy.Engine:
        model_dict = self.dict(
            exclude=self._get_exec_engine_excludes(),
            config_provider=self._config_provider,
        )

        connection_string = model_dict.pop("connection_string")
        # is connection_string was a ConfigStr it's parts will not have been validated yet
        if not isinstance(connection_string, DatabricksDsn):
            connection_string = DatabricksDsn.parse_url(
                url=connection_string, config_provider=self._config_provider
            )

        kwargs = model_dict.pop("kwargs", {})

        http_path = _parse_param_from_query_string(param="http_path", query=connection_string.query)
        assert http_path, "Presence of http_path query string is guaranteed due to prior validation"

        # Databricks connection is a bit finicky - the http_path portion of the connection string needs to be passed in connect_args  # noqa: E501 # FIXME CoP
        connect_args = {"http_path": http_path}
        return sa.create_engine(connection_string, connect_args=connect_args, **kwargs)
