import glob
import nox

nox.options.default_venv_backend = "uv"

@nox.session(venv_backend="none")
def dashboards(session):
    """Run the panel dashboards."""
    dashboard_files = [f for f in glob.glob("dashboards/*.py") if not f.endswith("common.py")]
    
    session.run(
        "panel",
        "serve",
        *dashboard_files,
        "--index",
        "dashboards/index.html",
        "--static-dirs",
        "thumbnails=./dashboards/thumbnails",
        "--autoreload",
        "--warm",
        external=True,
    )
