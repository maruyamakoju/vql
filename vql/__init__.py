"""VQL — Video Query Language.

Compile a video once into a VIR (Video Intermediate Representation),
then query it deterministically in <10 ms — no model required at query time.

    from vql.vir      import VIR
    from vql.parser   import parse_vql
    from vql.executor import VQLExecutor

    vir      = VIR.from_json("entrance_cam.vir.json")
    query    = parse_vql('''
        SELECT   person
        FROM     VIR("entrance_cam_2h.mp4")
        WHERE    ENTERS(person, zone("A区域"),
                        time_range(from="14:00:00", to="15:00:00"))
          AND    DURATION(person, zone("A区域")) < 5min
        RETURN   track_id, enter_t, exit_t, duration, evidence_frames(n=2)
    ''')
    result   = VQLExecutor().execute(query, vir)
    print(result)          # deterministic, reproducible
"""

__version__ = "0.1.0"
__all__ = ["VIR", "parse_vql", "VQLSyntaxError", "VQLExecutor"]

from vql.vir      import VIR           # noqa: F401
from vql.parser   import parse_vql, VQLSyntaxError   # noqa: F401
from vql.executor import VQLExecutor   # noqa: F401
