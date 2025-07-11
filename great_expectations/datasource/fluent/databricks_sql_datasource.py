from __future__ import annotations

import logging
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
        logger = logging.getLogger(__name__)
        logger.debug(f"DATABRICKS DEBUG: _resolve_quoted_name called with table_name={table_name!r} (type: {type(table_name)})")
        
        from great_expectations.compatibility import sqlalchemy

        # If it's already a quoted_name, return as-is
        if sqlalchemy.quoted_name and isinstance(table_name, sqlalchemy.quoted_name):
            logger.debug(f"DATABRICKS DEBUG: table_name is already quoted_name, returning as-is: {table_name!r}")
            return table_name

        # Check if the table name is quoted/bracketed (including Databricks backticks)
        table_name_is_quoted = cls._is_bracketed_by_quotes(table_name)
        logger.debug(f"DATABRICKS DEBUG: table_name_is_quoted={table_name_is_quoted}")

        if sqlalchemy.quoted_name:  # type: ignore[truthy-function]
            logger.debug("DATABRICKS DEBUG: sqlalchemy.quoted_name is available")
            
            if table_name_is_quoted:
                # Handle different quote types - strip them and mark as quoted
                clean_table_name = table_name.strip('"').strip("'").strip("`")
                result = sqlalchemy.quoted_name(value=clean_table_name, quote=True)
                logger.debug(f"DATABRICKS DEBUG: Stripped existing quotes, created quoted_name(value={clean_table_name!r}, quote=True) -> {result!r}")
                return result

            # Check if Databricks backticks are needed based on content
            needs_backticks = cls._needs_databricks_backticks(table_name)
            logger.debug(f"DATABRICKS DEBUG: _needs_databricks_backticks returned {needs_backticks}")
            
            if needs_backticks:
                result = sqlalchemy.quoted_name(value=table_name, quote=True)
                logger.debug(f"DATABRICKS DEBUG: Created quoted_name(value={table_name!r}, quote=True) -> {result!r}")
                return result
            else:
                logger.debug(f"DATABRICKS DEBUG: No special quoting needed, returning plain table_name: {table_name!r}")
        else:
            logger.debug("DATABRICKS DEBUG: sqlalchemy.quoted_name not available, returning plain string")

        logger.debug(f"DATABRICKS DEBUG: Returning unchanged table_name: {table_name!r}")
        return table_name

    @pydantic.validator("schema_name", pre=True)
    def _resolve_schema_quoted_name(cls, schema_name: str | None) -> str | quoted_name | None:
        """Resolve quoted names for schema and handle Databricks backtick notation."""
        logger = logging.getLogger(__name__)
        logger.debug(f"DATABRICKS DEBUG: _resolve_schema_quoted_name called with schema_name={schema_name!r} (type: {type(schema_name)})")
        
        if schema_name is None:
            logger.debug("DATABRICKS DEBUG: schema_name is None, returning None")
            return None

        from great_expectations.compatibility import sqlalchemy

        # If it's already a quoted_name, return as-is
        if sqlalchemy.quoted_name and isinstance(schema_name, sqlalchemy.quoted_name):
            logger.debug(f"DATABRICKS DEBUG: schema_name is already quoted_name, returning as-is: {schema_name!r}")
            return schema_name

        # Check if the schema name is quoted/bracketed
        schema_name_is_quoted = cls._is_bracketed_by_quotes(schema_name)
        logger.debug(f"DATABRICKS DEBUG: schema_name_is_quoted={schema_name_is_quoted}")

        if sqlalchemy.quoted_name:  # type: ignore[truthy-function]
            logger.debug("DATABRICKS DEBUG: sqlalchemy.quoted_name is available for schema")
            
            if schema_name_is_quoted:
                # Handle different quote types - strip them and mark as quoted
                clean_schema_name = schema_name.strip('"').strip("'").strip("`")
                result = sqlalchemy.quoted_name(value=clean_schema_name, quote=True)
                logger.debug(f"DATABRICKS DEBUG: Stripped existing quotes from schema, created quoted_name(value={clean_schema_name!r}, quote=True) -> {result!r}")
                return result

            # Check if Databricks backticks are needed based on content
            needs_backticks = cls._needs_databricks_backticks(schema_name)
            logger.debug(f"DATABRICKS DEBUG: _needs_databricks_backticks for schema returned {needs_backticks}")
            
            if needs_backticks:
                result = sqlalchemy.quoted_name(value=schema_name, quote=True)
                logger.debug(f"DATABRICKS DEBUG: Created schema quoted_name(value={schema_name!r}, quote=True) -> {result!r}")
                return result
        else:
            logger.debug("DATABRICKS DEBUG: sqlalchemy.quoted_name not available for schema, returning plain string")

        logger.debug(f"DATABRICKS DEBUG: Returning unchanged schema_name: {schema_name!r}")
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
        logger = logging.getLogger(__name__)
        logger.debug(f"DATABRICKS DEBUG: _needs_databricks_backticks checking name={name!r}")

        # Check if name starts with a number
        starts_with_digit = bool(re.match(r"^\d", name))
        logger.debug(f"DATABRICKS DEBUG: starts_with_digit={starts_with_digit}")
        if starts_with_digit:
            logger.debug(f"DATABRICKS DEBUG: Name starts with digit, needs backticks: {name!r}")
            return True

        # Check if name contains special characters that need escaping
        has_special_chars = bool(re.search(r"[.\s\-#@]", name))
        logger.debug(f"DATABRICKS DEBUG: has_special_chars={has_special_chars}")
        if has_special_chars:
            logger.debug(f"DATABRICKS DEBUG: Name has special characters, needs backticks: {name!r}")
            return True

        logger.debug(f"DATABRICKS DEBUG: Name does not need backticks: {name!r}")
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
        logger = logging.getLogger(__name__)
        logger.debug(f"DATABRICKS DEBUG: _is_bracketed_by_quotes checking target={target!r}")
        
        # Check standard quotes
        from great_expectations.datasource.fluent.sql_datasource import DEFAULT_QUOTE_CHARACTERS
        for quote in DEFAULT_QUOTE_CHARACTERS:
            if target.startswith(quote) and target.endswith(quote):
                logger.debug(f"DATABRICKS DEBUG: Found standard quotes '{quote}' around target: {target!r}")
                return True

        # Check Databricks backticks
        if target.startswith("`") and target.endswith("`"):
            logger.debug(f"DATABRICKS DEBUG: Found Databricks backticks around target: {target!r}")
            return True

        logger.debug(f"DATABRICKS DEBUG: Target is not bracketed by quotes: {target!r}")
        return False

    @override 
    def _create_batch_spec_kwargs(self) -> dict[str, Any]:
        """Override to add debug logging for batch spec creation."""
        from typing import Any
        logger = logging.getLogger(__name__)
        
        logger.debug(f"DATABRICKS DEBUG: _create_batch_spec_kwargs called for asset '{self.name}'")
        logger.debug(f"DATABRICKS DEBUG: self.table_name={self.table_name!r} (type: {type(self.table_name)})")
        logger.debug(f"DATABRICKS DEBUG: self.schema_name={self.schema_name!r} (type: {type(self.schema_name)})")
        
        # Use fallback logic like qualified_name and as_selectable
        schema_name = self.schema_name
        if schema_name is None and hasattr(self.datasource, 'schema_') and self.datasource.schema_:
            schema_name = self.datasource.schema_
            logger.debug(f"DATABRICKS DEBUG: Using datasource schema fallback: {schema_name!r}")
        
        table_name_str = str(self.table_name)
        schema_name_str = str(schema_name) if schema_name else None
        
        logger.debug(f"DATABRICKS DEBUG: Final batch spec values - table_name_str={table_name_str!r}, schema_name_str={schema_name_str!r}")
        
        result = {
            "type": "table",
            "data_asset_name": self.name,
            "table_name": table_name_str,
            "schema_name": schema_name_str,
            "batch_identifiers": {},
        }
        
        logger.debug(f"DATABRICKS DEBUG: _create_batch_spec_kwargs returning: {result}")
        return result

    @override
    def as_selectable(self) -> sqlalchemy.Selectable:
        """Override to add debug logging for selectable creation."""
        logger = logging.getLogger(__name__)
        logger.debug(f"DATABRICKS DEBUG: as_selectable called for asset '{self.name}'")
        logger.debug(f"DATABRICKS DEBUG: Using table_name={self.table_name!r}, schema_name={self.schema_name!r}")
        
        # Call parent implementation
        result = super().as_selectable()
        logger.debug(f"DATABRICKS DEBUG: as_selectable created selectable: {result}")
        return result


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
