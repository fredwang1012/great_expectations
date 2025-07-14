#!/usr/bin/env python3
"""
Debug script for Databricks Custom SQL Expectations with special table names.
This script simulates the Custom SQL expectation flow with detailed logging.
"""

import logging
import sys
from unittest.mock import Mock, patch

# Configure logging to show debug messages
logging.basicConfig(
    level=logging.DEBUG,
    format='%(levelname)s - %(message)s',
    stream=sys.stdout
)

print("=== Databricks Custom SQL Debug Script ===\n")

# Import necessary components
try:
    from great_expectations.datasource.fluent.databricks_sql_datasource import (
        DatabricksTableAsset,
        DatabricksSQLDatasource
    )
    from great_expectations.expectations.metrics.query_metric_provider import QueryMetricProvider
    from great_expectations.compatibility.sqlalchemy import sqlalchemy as sa
    from great_expectations.execution_engine.sqlalchemy_dialect import GXSqlDialect
    print("✅ Successfully imported Great Expectations components")
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Make sure you're running this from the great_expectations directory")
    sys.exit(1)

# Check if databricks-sqlalchemy is installed
try:
    from databricks.sqlalchemy import DatabricksDialect
    databricks_available = True
    print("✅ databricks-sqlalchemy is installed")
except ImportError:
    databricks_available = False
    print("⚠️  databricks-sqlalchemy is NOT installed - will simulate")

print("\n--- Testing Table Name Processing ---")

# Test table name that starts with a digit
test_table_name = "247_alto_ratios"
print(f"\nTest table: {test_table_name}")

# Test the validator
print("\n1. Testing DatabricksTableAsset validator:")
needs_backticks = DatabricksTableAsset._needs_databricks_backticks(test_table_name)
print(f"   - Needs backticks? {needs_backticks}")

# Simulate the validator processing
quoted_name_result = DatabricksTableAsset._resolve_quoted_name(test_table_name)
print(f"   - Validator result: {quoted_name_result}")
print(f"   - Result type: {type(quoted_name_result)}")
if hasattr(quoted_name_result, 'quote'):
    print(f"   - Has quote attribute: {quoted_name_result.quote}")

print("\n2. Testing QueryMetricProvider SQL generation:")

# Create a mock execution engine
mock_engine = Mock()

if databricks_available:
    # Use real Databricks dialect
    mock_engine.dialect = DatabricksDialect()
    print("   - Using REAL Databricks dialect")
else:
    # Simulate Databricks dialect
    mock_preparer = Mock()
    mock_preparer.initial_quote = '`'
    mock_preparer.final_quote = '`'
    mock_preparer.quote = lambda x: f"`{x}`"
    
    mock_dialect = Mock()
    mock_dialect.identifier_preparer = mock_preparer
    mock_engine.dialect = mock_dialect
    print("   - Using SIMULATED Databricks dialect")

# Test different table creation methods
print("\n3. Testing different table object types:")

# Method 1: Using sa.table() (creates TableClause)
table_clause = sa.table(quoted_name_result)
print(f"\n   a) sa.table() result:")
print(f"      - Type: {type(table_clause)}")
print(f"      - Name: {table_clause.name}")
print(f"      - String representation: {table_clause}")

# Test QueryMetricProvider with TableClause
query_template = "SELECT * FROM {batch} WHERE true"
result_query = QueryMetricProvider._get_substituted_batch_subquery_from_query_and_batch_selectable(
    query=query_template,
    batch_selectable=table_clause,
    execution_engine=mock_engine
)
print(f"\n   b) Generated SQL with TableClause:")
print(f"      {result_query}")

# Check if backticks are used
if "`247_alto_ratios`" in result_query:
    print("      ✅ SUCCESS: Backticks are used!")
else:
    print("      ❌ FAILED: Double quotes still being used")

# Method 2: Using sa.Table() (creates Table object)
try:
    table_obj = sa.Table(quoted_name_result, sa.MetaData())
    print(f"\n   c) sa.Table() result:")
    print(f"      - Type: {type(table_obj)}")
    print(f"      - Name: {table_obj.name}")
    
    result_query2 = QueryMetricProvider._get_substituted_batch_subquery_from_query_and_batch_selectable(
        query=query_template,
        batch_selectable=table_obj,
        execution_engine=mock_engine
    )
    print(f"\n   d) Generated SQL with Table object:")
    print(f"      {result_query2}")
except Exception as e:
    print(f"\n   c) Error with sa.Table(): {e}")

print("\n4. Testing with schema:")
# Test with schema
schema_name = "my_catalog"
table_with_schema = sa.table(quoted_name_result, schema=schema_name)
print(f"   - Table with schema: {table_with_schema}")

result_with_schema = QueryMetricProvider._get_substituted_batch_subquery_from_query_and_batch_selectable(
    query=query_template,
    batch_selectable=table_with_schema,
    execution_engine=mock_engine
)
print(f"   - Generated SQL: {result_with_schema}")

print("\n--- Summary ---")
print("\nThe fix ensures that:")
print("1. DatabricksTableAsset validator returns quoted_name(quote=True) for special names")
print("2. QueryMetricProvider uses the dialect's identifier preparer")
print("3. Databricks dialect uses backticks (`) instead of double quotes (\")")
print("\nYour Custom SQL Expectations should now generate:")
print("   SELECT * FROM `247_alto_ratios` WHERE true")
print("Instead of:")
print("   SELECT * FROM \"247_alto_ratios\" WHERE true")

print("\n--- Debug Tips ---")
print("1. Enable DEBUG logging in your application to see QueryMetricProvider logs")
print("2. Check that databricks-sqlalchemy is installed in your environment")
print("3. Verify the execution engine dialect is correctly set to Databricks")
print("4. Make sure these changes are deployed to your Databricks environment")
