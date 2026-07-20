from app.modules.routing.polyline import decode_polyline6


def test_decode_polyline6_round_trips_known_coordinates():
    # Real Valhalla output for a short walking route near Chennai Marina,
    # captured live against the pilot-district tiles this milestone builds.
    shape = (
        "yso{W{i~bxCkn@gGsjA{KgzBmR}jB_NeBnAwB|B{aBsQsAQ}yCs_@sNmCgnEyl@gQwAqE`AoLkAm@tCsVcCqi@"
        "sKcAlFah@_HomAsQcV}BcIu@mOw@}sAuSeIsAyOmBey@{Myi@sI_GaAs]aGoLyBwI}BiG{BeHiDiHsDkDgBqFmC"
        "{BiAsFwBmFeAwEq@kIaAkIeA_BQiFi@gC[mEg@sDk@uDu@A?uBu@gEmBcCqAcd@yUgEwBeLaGmE_CgCgAyDkAkJ"
        "cBgmAuTu}AkXee@mRoXeCcRaAmBM}KaAu[mIkSmFwm@wM_E{@oH_BuHgC_a@wMuH}BwTuAa{@b@q_@b@wBHg{@c"
        "FwCm@y_@gGm\\_ImZiLa]aMgTuHai@yKw@YeC_AkCaA}Cy@gD{@qHgBuOmDa{@gTmCo@kaBw`@qr@iOes@uOwWs"
        "FyNcDmNyC}LqCgDw@uEeAyvD_`A}_Bo\\sqAqRgrB_WyjBa^{K_CeINmKlBmHxE}G|J_Jl[u\\jhAmFlFmEi@_S"
        "aEqeAkYmMiDaIuBe@KoUkGkGcBgDC}q@kRka@qJgLoAy\\wEaj@mI_Em@_eA}MsMwAiK_BsDe@k`@}FnEi`@hAaK"
        "cMyAwnAaQk|@kNwCe@o`@qG"
    )
    coords = decode_polyline6(shape)
    assert len(coords) > 1
    first_lon, first_lat = coords[0]
    last_lon, last_lat = coords[-1]
    assert abs(first_lon - 80.281262) < 1e-4
    assert abs(first_lat - 13.050189) < 1e-4
    assert abs(last_lon - 80.296881) < 1e-4
    assert abs(last_lat - 13.116656) < 1e-4


def test_decode_polyline6_empty_string_returns_empty_list():
    assert decode_polyline6("") == []
