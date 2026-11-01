"""Explicit user-requested graph-policy transitions, never blanket failure ignores."""
PREFIX = 'tests.test_reference_harness_policy_offline.ReferenceHarnessPolicyTests.'
REPORT_POLICY = {
    'tests.test_report_visual_selection_offline.ReportVisualSelectionTests.test_real_two_prices_are_visible_table_without_chart_or_hidden_values':
        PREFIX+'test_price_plot_occurs_once_beside_answer_and_preserves_all_bindings',
    'tests.test_report_visual_selection_offline.ReportVisualSelectionTests.test_single_period_financial_inputs_are_not_a_large_chart':
        PREFIX+'test_financial_comparison_has_exact_values_and_accounting_basis',
    'tests.test_report_visual_selection_offline.ReportVisualSelectionTests.test_negative_cash_stays_negative_in_visible_table':
        PREFIX+'test_negative_cash_plot_and_its_data_table_remain_negative',
    'tests.test_report_visual_selection_offline.ReportVisualSelectionTests.test_fee_chart_groups_original_components_by_fund_not_by_row':
        PREFIX+'test_fee_components_have_actual_object_groups_and_no_temporal_series',
    'tests.test_report_visual_selection_offline.ReportVisualSelectionTests.test_index_table_preserves_variant_currency_and_base':
        PREFIX+'test_index_observations_retain_variant_currency_base_and_dates',
    'tests.test_report_measurement_accessibility_offline.ReportMeasurementAccessibilityTests.test_sparse_tables_have_actual_names_and_column_headers_without_new_copy':
        PREFIX+'test_every_measurement_keeps_accessible_table_units_headers_and_evidence',
    'tests.test_report_editorial_figures_offline.EditorialFigureTests.test_disclosed_lower_bound_and_units_survive_accessible_table':
        PREFIX+'test_five_ai_figures_have_matching_tables_and_original_lower_bound',
    'tests.test_report_argument_figures_offline.ArgumentFigureTests.test_each_figure_belongs_to_its_research_argument':
        PREFIX+'test_all_six_figure_argument_bindings_are_complete_and_unique',
    'tests.test_report_editorial_figures_offline.EditorialFigureTests.test_missing_values_do_not_become_zero_or_a_revenue_path':
        PREFIX+'test_actual_historical_series_are_not_pixel_reconstructed_or_interpolated',
}


def lineage(retained):
    current = {old: [REPORT_POLICY.get(check, check) for check in checks]
               for old, checks in retained.items()}
    for old, check in REPORT_POLICY.items():
        assert old not in current, 'A policy transition must have one explicit owner'
        current[old] = [check]
    return current
