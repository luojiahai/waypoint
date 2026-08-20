import pytest

from waypoint.metrics import charts


def test_wip_bar_under_limit_fills_proportionally_and_is_green():
    bar = charts.wip_bar("In Progress", count=2, limit=4, width=180)
    assert bar.fill_width == 90.0
    assert bar.fill_color == charts.FILL_OK
    assert bar.over is False
    assert bar.tick_x == 180.0
    assert bar.label == "2 / 4"


def test_wip_bar_at_the_limit_is_amber():
    assert charts.wip_bar("x", count=4, limit=4).fill_color == charts.FILL_MED


def test_wip_bar_over_the_limit_clamps_the_fill_and_flags_over():
    bar = charts.wip_bar("x", count=9, limit=4, width=180)
    assert bar.fill_width == 180.0
    assert bar.over is True
    assert bar.fill_color == charts.FILL_MED


def test_a_column_with_no_limit_says_so_and_has_no_tick():
    bar = charts.wip_bar("To Do", count=7, limit=None, width=180)
    assert bar.no_limit is True
    assert bar.tick_x is None
    assert bar.fill_color == charts.TEXT_3
    assert bar.label == "7 · no limit"
    assert bar.over is False


def test_zero_limit_is_treated_as_no_limit():
    assert charts.wip_bar("x", count=3, limit=0).no_limit is True


def test_sparkline_maps_values_across_the_box():
    spark = charts.sparkline([0, 5, 10], width=200, height=42)
    pairs = [tuple(float(n) for n in point.split(",")) for point in spark.points.split()]
    assert pairs[0][0] == 0.0
    assert pairs[-1][0] == 200.0
    assert pairs[0][1] > pairs[-1][1]  # larger values sit higher on the page
    assert spark.has_data is True


def test_sparkline_of_a_flat_series_sits_on_the_mid_line():
    spark = charts.sparkline([4, 4, 4], height=42)
    ys = {float(point.split(",")[1]) for point in spark.points.split()}
    assert ys == {21.0}


def test_sparkline_skips_missing_points_rather_than_drawing_them_as_zero():
    spark = charts.sparkline([1, None, 3])
    assert len(spark.points.split()) == 2


def test_empty_sparkline_reports_no_data():
    spark = charts.sparkline([])
    assert spark.has_data is False
    assert spark.points == ""


def test_p75_series_is_returned_separately_for_the_dashed_stroke():
    spark = charts.sparkline([1, 2], [3, 4])
    assert spark.p75_points.count(",") == 2


def test_bar_spark_scales_bars_to_the_tallest_count():
    spark = charts.bar_spark([2, 4], width=200, height=42)
    assert [bar.height for bar in spark.bars] == [21.0, 42.0]
    assert spark.bars[0].y == 21.0


def test_bar_spark_of_all_zeroes_draws_no_height():
    assert [bar.height for bar in charts.bar_spark([0, 0]).bars] == [0.0, 0.0]


def test_progress_bar_fills_by_ratio_and_clamps():
    assert charts.progress_bar(3, 4, width=120).fill_width == 90.0
    assert charts.progress_bar(5, 4, width=120).fill_width == 120.0
    assert charts.progress_bar(0, 0, width=120).fill_width == 0.0


def test_aging_chart_places_dots_by_age_and_lanes_by_column():
    lanes = [
        charts.AgingLaneInput("In Progress", "2 / 4", [charts.AgingItem("PROJ-1", 4.0)]),
        charts.AgingLaneInput("Review", "1 / 3", [charts.AgingItem("PROJ-2", 20.0)]),
    ]
    chart = charts.aging_chart(lanes, threshold_days=10, width=520, lane_height=34)
    assert len(chart.lanes) == 2
    assert chart.lanes[0].dots[0].cx < chart.lanes[1].dots[0].cx
    assert chart.lanes[1].y > chart.lanes[0].y


def test_aging_dot_colour_steps_at_the_threshold_and_at_twice_it():
    lanes = [charts.AgingLaneInput("c", "", [
        charts.AgingItem("a", 5.0), charts.AgingItem("b", 12.0), charts.AgingItem("c", 25.0)
    ])]
    colors = [dot.color for dot in charts.aging_chart(lanes, threshold_days=10).lanes[0].dots]
    assert colors == [charts.DOT_UNDER, charts.MED, charts.HIGH]


def test_aging_chart_annotates_the_oldest_item():
    lanes = [charts.AgingLaneInput("c", "", [
        charts.AgingItem("PROJ-1", 4.0), charts.AgingItem("PROJ-9", 21.0)
    ])]
    chart = charts.aging_chart(lanes, threshold_days=10)
    assert chart.oldest_label == "PROJ-9 · 21d"


def test_aging_chart_marks_the_threshold_and_carries_four_x_ticks():
    chart = charts.aging_chart(
        [charts.AgingLaneInput("c", "", [charts.AgingItem("a", 20.0)])], threshold_days=10
    )
    assert 0 < chart.threshold_x < chart.width
    assert chart.threshold_days == 10
    assert len(chart.x_ticks) == 4


def test_aging_chart_with_no_items_reports_no_data():
    chart = charts.aging_chart([charts.AgingLaneInput("c", "", [])], threshold_days=10)
    assert chart.has_data is False


def test_no_chart_function_accepts_a_goal_line():
    import inspect

    for name in ("sparkline", "bar_spark", "progress_bar", "wip_bar", "aging_chart"):
        params = inspect.signature(getattr(charts, name)).parameters
        assert not any("goal" in p or "target" in p for p in params)
