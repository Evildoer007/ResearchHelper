"""Legacy suite name; canonical current assertions run once during collection."""
from tests import test_strategy_report_figures_offline as original


class CurrentStrategyCharts(original.StrategyReportFigureTests):
    # Keep historical suite imports available without collecting every
    # inherited test a second time. No reader-version text substitutions.
    __test__ = False
