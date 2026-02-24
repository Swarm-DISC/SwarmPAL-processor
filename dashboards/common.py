from collections import namedtuple
from pathlib import Path
from tempfile import NamedTemporaryFile

from jinja2 import Environment, FileSystemLoader
import panel as pn



HEADER = pn.pane.Markdown(
    """
    *This dashboard is in active development and is provided here for testing purposes  
    Contact: ashley.smith@ed.ac.uk*

    [..to the other dashboards](../) &nbsp;&nbsp;&nbsp;&nbsp;  [SwarmPAL Docs](https://swarmpal.readthedocs.io) &nbsp;&nbsp;&nbsp;&nbsp;  [<img src="https://github.githubassets.com/favicons/favicon.svg" width="16" height="16" alt="GitHub"/> SwarmPAL](https://github.com/Swarm-DISC/SwarmPAL) &nbsp;&nbsp;&nbsp;&nbsp;  [<img src="https://github.githubassets.com/favicons/favicon.svg" width="16" height="16" alt="GitHub"/> SwarmPAL-processor](https://github.com/Swarm-DISC/SwarmPAL-processor)
    """,
    styles={
        "color": "white",
        "background-color": "#003757",
        "font-size": "15px",
        "border-radius": "10px",
        "padding-left": "10px",
        "padding-right": "10px",
        "padding-top": "0px",
        "padding-bottom": "0px",
    },
    dedent=True,
    renderer="markdown",
)


CODE_TEMPLATE_DIR = Path(__file__).parent / Path("code_templates")
JINJA2_ENVIRONMENT = Environment(loader=FileSystemLoader(CODE_TEMPLATE_DIR))


class CustomisedFileDropper(pn.widgets.FileDropper):
    """Custom FileDropper widget to handle file uploads and temporary file creation."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._temp_file = None
        self.param.watch(self.update_temp_file, 'value')

    @property
    def temp_file(self):
        """Access the stored temporary file"""
        return self._temp_file

    def update_temp_file(self, event):
        """Write a temporary file to disk so that SwarmPAL CDF reader can read it."""
        if self.value:
            self._temp_file = NamedTemporaryFile(
                prefix=Path(self.file_in_mem.name).stem,
                suffix=Path(self.file_in_mem.name).suffix,
            )
            with open(self._temp_file.name, 'wb') as f:
                f.write(self.file_in_mem.content)
        else:
            self._temp_file = None
    
    @property
    def file_in_mem(self):
        """Accessed as: file_in_mem.name and file_in_mem.content"""
        file_name, file_content = next(iter(self.value.items()))
        File = namedtuple('File', ['name', 'content'])
        file = File(file_name, file_content)
        return file