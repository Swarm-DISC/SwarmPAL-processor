import datetime as dt
import matplotlib.pyplot as plt
import panel as pn
import pprint
from bokeh.models.formatters import PrintfTickFormatter
from pathlib import Path
import re
import swarmpal
from swarmpal.utils.configs import SPACECRAFT_TO_MAGLR_DATASET
from yaml import dump

from common import HEADER, JINJA2_ENVIRONMENT, CustomisedFileDropper

TFA_VIRES_CODE_TEMPLATE = "tfa_vires.jinja2"
TFA_CDF_CODE_TEMPLATE = "tfa_cdf.jinja2"
FAC_SINGLE_SAT_CLI_TEMPLATE = "fac-single-sat-cli.jinja2"

# TODO: Some of the date picker functionality is copied from the FAC dashboard and can be moved to common.py
start_of_today = dt.datetime.now().date()
end_of_today = start_of_today + dt.timedelta(days=1)
two_days_ago = start_of_today - dt.timedelta(days=2)
yesterday = start_of_today - dt.timedelta(days=1)
four_weeks_ago = end_of_today - dt.timedelta(days=28)

widgets = {
    # For dataset params
    "spacecraft": pn.widgets.RadioBoxGroup(options=['Swarm-A', 'Swarm-B', 'Swarm-C'], value='Swarm-A'),
    "start-end": pn.widgets.DatetimeRangePicker(
        name="Select time range",
        start=dt.datetime(2000, 1, 1, 0, 0),
        end=dt.datetime.combine(end_of_today, dt.time(23, 59)),
        value=(dt.datetime(2026, 1, 1, 0, 0), dt.datetime(2026, 1, 2, 0, 0)),
        enable_time=True,
        enable_seconds=False,
    ),
    "button-fetch-data": pn.widgets.Button(name="Fetch inputs", button_type="primary"),
    "button-run-analysis": pn.widgets.Button(name="Run analysis", button_type="primary"),
    "file-dropper": CustomisedFileDropper(multiple=False),
    # For TFA_Preprocess params
    "preprocess-active-component": pn.widgets.DiscreteSlider(
        name="Active Component", 
        options=[0, 1,2], 
        value=2,
    ),
    "preprocess-sampling-rate": pn.widgets.EditableFloatSlider(
        name="Sampling Rate", 
        format=PrintfTickFormatter(format='%.0f Hz'), 
        value=1.0,
        step=1.0,
        start=0.1,
        end=10.0,
    ),
    # For TFA_Clean params
    "clean-window-size": pn.widgets.EditableIntSlider(
        name="Window Size",
        start=100,
        end=1000,
        step=100,
        value=300,
    ),
    "clean-multiplier": pn.widgets.EditableFloatSlider(
        name="Multiplier", 
        format=PrintfTickFormatter(format='%.1f'), 
        value=0.5,
        step=0.1,
        start=0.1,
        end=2.0,
    ),
    "clean-method": pn.widgets.Select(
        name='Method', 
        options=['iqr', 'normal'],
        value='iqr',
    ),
    # For TFA_Filter params
    "filter-cutoff": pn.widgets.EditableFloatSlider(
        name="Cut off frequency", 
        format=PrintfTickFormatter(format='%.03f Hz'), 
        value=0.02,
        step=0.001,
        start=0.001,
        end=0.2,
    ),
    # For TFA_Wavelet params
    "wavelet-min-frequency" : pn.widgets.EditableFloatSlider(
        name="Minimum frequency", 
        format=PrintfTickFormatter(format='%.03f Hz'), 
        value=0.02,
        step=0.001,
        start=0.0,
        end=0.2,
    ),
    "wavelet-max-frequency" : pn.widgets.EditableFloatSlider(
        name="Maximum frequency", 
        format=PrintfTickFormatter(format='%.03f Hz'), 
        value=0.1,
        step=0.001,
        start=0.0,
        end=0.2,
    ),
    "wavelet-dj" : pn.widgets.EditableFloatSlider(
        name="DJ", 
        format=PrintfTickFormatter(format='%.02f'), 
        value=0.1,
        step=0.01,
        start=0.0,
        end=1.0,
    ),
}



def pprinter(object):
    '''Helper function to pretty print nested dicts and lists.

    Similar Python's built in pprint module, but uses the 'dict' constructor
    for dictionaries instead of curly brace syntax.
    '''
    def _newline(indent):
        return '\n' + (' ' * indent)
    def _pprinter(obj, indent):
        result = ''
        if isinstance(obj, dict):
            result += 'dict(' #)
            for item in obj:
                result += _newline(indent+4) + item + '='
                result += _pprinter(obj[item], indent+4)
                result += ","
            return result + _newline(indent) + ")"
        if isinstance(obj, list):
            result += '[' #]
            for item in obj:
                result += _newline(indent+4)
                result += _pprinter(item, indent+4)
                result += ','
            return result + _newline(indent) + ']'
        return repr(obj)

    return _pprinter(object, 0)

class TFA_GUI:
    def __init__(self, widgets):
        self.widgets = widgets

        self.output_title = pn.pane.Markdown()
        self.swarmpal_quicklook = pn.pane.Matplotlib(max_width=1000, sizing_mode='scale_width', align='center', margin=(10, 5))
        self.data_view = pn.pane.HTML()
        self.code_snippet = pn.pane.Markdown(styles={"font-size": "15px",})
        self.cli_command = pn.pane.Markdown(styles={"font-size": "15px",})
        self.log_messages = pn.pane.HTML(
            "",
            styles={
                'font-family': 'monospace',
                'font-size': '12px',
                'max-height': '400px',
                'overflow-y': 'auto',
                'background': '#f5f5f5',
                'padding': '10px',
                'min-width': '600px'
            }
        )
        
        # Create log modal and button
        self.log_modal = pn.Column(
            self.log_messages,
            styles={'min-height': '300px'}
        )
        self.log_button = pn.widgets.Button(
            name="📋 View Logs",
            button_type="light",
            width=140,
            margin=(5, 10)
        )
        self._is_loading = False

        self.widgets["button-fetch-data"].on_click(self.update_input_data)
        self.widgets["button-run-analysis"].on_click(self.update_analysis)
        
        # Watch process parameter widgets to auto-run analysis when changed
        self.widgets["preprocess-active-component"].param.watch(self.update_analysis, 'value')
        self.widgets["preprocess-sampling-rate"].param.watch(self.update_analysis, 'value')
        self.widgets["clean-method"].param.watch(self.update_analysis, 'value')
        self.widgets["clean-window-size"].param.watch(self.update_analysis, 'value')
        self.widgets["clean-multiplier"].param.watch(self.update_analysis, 'value')
        self.widgets["filter-cutoff"].param.watch(self.update_analysis, 'value')
        self.widgets["wavelet-min-frequency"].param.watch(self.update_analysis, 'value')
        self.widgets["wavelet-max-frequency"].param.watch(self.update_analysis, 'value')
        self.widgets["wavelet-dj"].param.watch(self.update_analysis, 'value')
        
        self.data = None
        self.raw_data = None  # Store raw data before processing
        self.data_tabs = None
        
        # Try to load from cache first for faster startup
        cache_key = "tfa_precache"
        if cache_key in pn.state.cache:
            try:
                self.log("Loading from cache...")
                cached = pn.state.cache[cache_key]
                self.raw_data = cached['raw_data']
                self.data = cached['data']
                self.swarmpal_quicklook.object = cached['figure']
                self.data_view.object = cached['data_view']
                self.code_snippet.object = cached['code_snippet']
                self.cli_command.object = cached['cli_command']
                self.output_title.object = "# SwarmPAL TFA Quicklook"
                self.log("Loaded from cache successfully", level="success")
            except Exception as e:
                self.log(f"Failed to load from cache: {e}", level="warning")
                # Fall through to normal loading
                self._load_initial_data()
        else:
            # Load data on startup and cache it
            self._load_initial_data()
    
    def _load_initial_data(self):
        """Load initial data and cache it for future sessions"""
        try:
            self.log("Starting TFA dashboard...")
            self.update_input_data(None)
            
            # Cache the loaded data for faster startup next time
            cache_key = "tfa_precache"
            pn.state.cache[cache_key] = {
                'raw_data': self.raw_data,
                'data': self.data,
                'figure': self.swarmpal_quicklook.object,
                'data_view': self.data_view.object,
                'code_snippet': self.code_snippet.object,
                'cli_command': self.cli_command.object,
            }
            self.log("Dashboard initialized successfully", level="success")
        except Exception as e:
            self.log(f"Failed to load initial data: {e}", level="error")
            import traceback
            self.log(traceback.format_exc(), level="error")

    @property
    def sidebar(self):
        '''Panel UI definition for the sidebar.'''
        self.data_tabs = pn.Tabs(
            ("VirES", pn.Column(
                self.widgets["start-end"],
                pn.pane.Markdown("Select spacecraft"),
                self.widgets["spacecraft"],
                )),
             ("CDF File", pn.Column(
                 pn.pane.Markdown("Upload CDF file:"),
                 self.widgets["file-dropper"],
                 )),
        )
        
        data_params_box = pn.Card(
            self.data_tabs,
            self.widgets["button-fetch-data"],
            title="Data Parameters",
            collapsed=False,
            collapsible=True,
            styles={'background': '#f0f8ff', 'border': '2px solid #4682b4', 'border-radius': '5px'}
        )
        
        process_params_box = pn.Column(
            pn.pane.Markdown("## Process Parameters"),
            pn.pane.Markdown("### Preprocess"),
            self.widgets['preprocess-active-component'],
            self.widgets['preprocess-sampling-rate'],
            pn.layout.Divider(),
            pn.pane.Markdown("### Clean"),
            self.widgets['clean-method'],
            self.widgets['clean-window-size'],
            self.widgets['clean-multiplier'],
            pn.layout.Divider(),
            pn.pane.Markdown("### Filter"),
            self.widgets['filter-cutoff'],
            pn.layout.Divider(),
            pn.pane.Markdown("### Wavelet"),
            self.widgets['wavelet-min-frequency'],
            self.widgets['wavelet-max-frequency'],
            self.widgets['wavelet-dj'],
            styles={'background': '#fff8f0', 'border': '2px solid #ff8c42', 'border-radius': '5px', 'padding': '10px'}
        )
        
        return pn.Column(
            data_params_box,
            pn.layout.Divider(),
            process_params_box,
        )

    def set_loading(self, loading=True):
        """Show or hide loading spinner in the log button"""
        self._is_loading = loading
        if loading:
            self.log_button.name = "⏳ Processing..."
            self.log_button.button_type = "warning"
        else:
            self.log_button.name = "📋 View Logs"
            self.log_button.button_type = "light"
    
    def log(self, message, level="info"):
        """Add a log message to the log panel"""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        
        colors = {
            "info": "#2c3e50",
            "success": "#27ae60",
            "warning": "#f39c12",
            "error": "#e74c3c"
        }
        color = colors.get(level, colors["info"])
        
        # Escape HTML in message
        import html
        escaped_message = html.escape(str(message))
        
        new_entry = f'<div style="color: {color}; margin-bottom: 5px;"><strong>[{timestamp}]</strong> {escaped_message}</div>'
        current_logs = self.log_messages.object or ""
        self.log_messages.object = current_logs + new_entry

    @property
    def main(self):
        return pn.Column(
            self.output_title,
            pn.Tabs(
                ("Quicklook", pn.Column(self.swarmpal_quicklook, align='center')),
                ("Data view", self.data_view),
                ("SwarmPAL Python Code", self.code_snippet),
                ("SwarmPAL CLI Command", self.cli_command),
            ),
        )

    def _get_data_product(self):
        '''Translates file input or the spacecraft radio group to a Swarm data product.'''
        if self._using_cdf_input():
            filename = self.widgets["file-dropper"].file_in_mem.name
            product_name_full = Path(filename).stem
            # Truncate to remove data and version
            return re.sub(r"_\d{8}T\d{6}.*$", "", product_name_full)

        spacecraft = self.widgets['spacecraft'].value
        if spacecraft == 'Swarm-A':
            return "SW_OPER_MAGA_LR_1B"
        if spacecraft == 'Swarm-B':
            return "SW_OPER_MAGB_LR_1B"
        if spacecraft == 'Swarm-C':
            return "SW_OPER_MAGC_LR_1B"
        return "SW_OPER_MAGA_LR_1B"
    
    def _make_vires_data_params(self):
        data_product = self._get_data_product()
        return [dict(
            provider="vires",
            collection=data_product,
            measurements=["B_NEC"],
            models=["Model='CHAOS-Core'+'CHAOS-Static'"],
            auxiliaries=["QDLat", "MLT"],
            start_time=self.widgets["start-end"].value[0].isoformat(),
            end_time=self.widgets["start-end"].value[1].isoformat(),
            pad_times=["03:00:00", "03:00:00"],
            server_url="https://vires.services/ows",
        )]


    def _make_cdf_data_params(self):
        filename = self.widgets["file-dropper"].temp_file.name
        product_name = self._get_data_product()

        return [dict(
            provider="file",
            filename=filename,
            filetype="cdf",
            dataset=product_name
        )]

    def _using_cdf_input(self):
        if self.data_tabs is None:
            return False
        return self.data_tabs.active == 1

    def _make_data_params(self):

        if self._using_cdf_input():
            return self._make_cdf_data_params()
        return self._make_vires_data_params()

    def make_config(self):
        '''Create a schema compatible data structure that describes the input dataset and SwarmPAL processes.'''
        data_product = self._get_data_product()
        data_params = self._make_data_params()
        process_params = [
            dict(
                process_name="TFA_Preprocess",
                dataset=data_product,
                active_variable="B_NEC",
                active_component=self.widgets['preprocess-active-component'].value,
                sampling_rate=self.widgets['preprocess-sampling-rate'].value,
                remove_model=False,
            ),
            dict(
                process_name="TFA_Clean",
                window_size=self.widgets['clean-window-size'].value,
                method=self.widgets['clean-method'].value,
                multiplier=self.widgets['clean-multiplier'].value,
            ),
            dict(
                process_name="TFA_Filter",
                cutoff_frequency=self.widgets['filter-cutoff'].value,
            ),
            dict(
                process_name="TFA_Wavelet",
                min_frequency=self.widgets['wavelet-min-frequency'].value,
                max_frequency=self.widgets['wavelet-max-frequency'].value,
                dj=self.widgets['wavelet-dj'].value,
            ),
        ]
        return dict(
            data_params=data_params,
            process_params=process_params,
        )

    def update_input_data(self, event):
        '''Downloads input data and applies processes'''

        if self._using_cdf_input() and not self.widgets["file-dropper"].value:
            return

        try:
            self.set_loading(True)
            self.log("Fetching input data...")
            config = self.make_config()
            self.raw_data = swarmpal.fetch_data(config)
            # Make a copy for processing
            import copy
            self.data = copy.deepcopy(self.raw_data)
            self.log("Applying processes...")
            swarmpal.apply_processes(self.data, config['process_params'])
            
            # Display fetched data info
            self.data_view.object = self.data._repr_html_()
            self.code_snippet.object = self.get_vires_code()
            self.log("Data fetched and processed successfully", level="success")
        except Exception as e:
            self.log(f"Error fetching data: {e}", level="error")
            import traceback
            self.log(traceback.format_exc(), level="error")
            raise
        finally:
            self.set_loading(False)
        
        # Automatically update the analysis with the new data
        self.update_analysis(None)

    def update_analysis(self, event, title="# SwarmPAL TFA Quicklook"):
        '''Analyze data and update the output pane'''

        if self.raw_data is None:
            self.output_title.object = "Please fetch data first"
            self.log("No data available for analysis", level="warning")
            return

        try:
            self.set_loading(True)
            self.log("Running analysis with current parameters...")
            # Re-apply processes with current parameters
            import copy
            config = self.make_config()
            self.data = copy.deepcopy(self.raw_data)
            swarmpal.apply_processes(self.data, config['process_params'])

            self.output_title.object = title

            # Update code and CLI snippets
            self.cli_command.object = self.get_cli()
            self.code_snippet.object = self.get_vires_code()

            if not self.data:
                return

            # Update the data view
            self.data_view.object = self.data._repr_html_()

            if self._using_cdf_input(): # TypeError: No numeric data to plot. when working on CDF files
                self.log("Analysis complete (CDF mode)", level="success")
                return

            # Plot the results
            self.log("Generating quicklook plot...")
            fig, _ = swarmpal.toolboxes.tfa.plotting.quicklook(
                self.data,
                tlims=(
                    self.widgets["start-end"].value[0].isoformat(),
                    self.widgets["start-end"].value[1].isoformat(),
                ),
                extra_x=('QDLat', 'MLT', 'Latitude'),
            )
            # Reduce figure size to fit on screen
            # fig.set_size_inches(12, 5)
            fig.tight_layout()
            self.swarmpal_quicklook.object = fig
            self.log("Analysis complete", level="success")
        except Exception as e:
            self.log(f"Error during analysis: {e}", level="error")
            import traceback
            self.log(traceback.format_exc(), level="error")
            raise
        finally:
            self.set_loading(False)

    @staticmethod
    def _empty_matplotlib_figure():
        fig, ax = plt.subplots()
        ax.set_axis_off()
        ax.text(0.5, 0.5, "No data available / error in figure creation", ha="center", va="center", fontsize=20)
        return fig

    def replace_file_paths(self, s):
        '''Replace tmp file locations with in memory file locations in a string s'''
        if not self._using_cdf_input():
            return s
        tmp_filename = self.widgets["file-dropper"].temp_file.name
        user_filename = self.widgets["file-dropper"].file_in_mem.name
        return s.replace(tmp_filename, user_filename)

    def get_vires_code(self):
        '''Updates the Python code snippet'''
        config = self.make_config()
        #config_code = pprint.pformat(config, sort_dicts=False)
        config_code = pprinter(config) #, sort_dicts=False)

        # Replace tmp (used for running on server) filename with file_in_mem (what the user will use) name
        config_code = self.replace_file_paths(config_code)

        context = dict(
            config=config_code,
        )
        template = JINJA2_ENVIRONMENT.get_template(TFA_VIRES_CODE_TEMPLATE)
        return f"```python\n{template.render(context)}\n```"
        #return f"```python\nprint('hello world')\n```"

    def get_cdf_code(self):
        pass

    def get_cli(self):
        '''Updates the CLI example snippet'''
        config = self.make_config()
        config_yaml = dump(config, sort_keys=False)

        # Replace tmp (used for running on server) filename with file_in_mem (what the user will use) name
        config_yaml = self.replace_file_paths(config_yaml)

        context = dict(
            config=config_yaml,
        )
        template = JINJA2_ENVIRONMENT.get_template(FAC_SINGLE_SAT_CLI_TEMPLATE)
        return template.render(context)


# Pre-populate cache at server startup for faster first load
def _populate_tfa_cache():
    """Pre-populate the TFA cache with default data"""
    cache_key = "tfa_precache"
    if cache_key not in pn.state.cache:
        print("Starting TFA precache...")
        try:
            # Create default config with sensible defaults (matching widget defaults)
            default_config = {
                'data_params': [dict(
                    provider="vires",
                    collection="SW_OPER_MAGA_LR_1B",
                    measurements=["B_NEC"],
                    models=["Model='CHAOS-Core'+'CHAOS-Static'"],
                    auxiliaries=["QDLat", "MLT"],
                    start_time=dt.datetime(2026, 1, 1).isoformat(),
                    end_time=dt.datetime(2026, 1, 2).isoformat(),
                    pad_times=["03:00:00", "03:00:00"],
                    server_url="https://vires.services/ows",
                )],
                'process_params': [
                    dict(
                        process_name="TFA_Preprocess",
                        dataset="SW_OPER_MAGA_LR_1B",
                        active_variable="B_NEC",
                        active_component=2,
                        sampling_rate=1.0,
                        remove_model=False,
                    ),
                    dict(
                        process_name="TFA_Clean",
                        window_size=300,
                        method='iqr',
                        multiplier=0.5,
                    ),
                    dict(
                        process_name="TFA_Filter",
                        cutoff_frequency=0.02,
                    ),
                    dict(
                        process_name="TFA_Wavelet",
                        min_frequency=0.02,
                        max_frequency=0.1,
                        dj=0.1,
                    ),
                ]
            }
            
            # Fetch and process data
            raw_data = swarmpal.fetch_data(default_config)
            import copy
            data = copy.deepcopy(raw_data)
            swarmpal.apply_processes(data, default_config['process_params'])
            
            # Generate quicklook plot
            fig, _ = swarmpal.toolboxes.tfa.plotting.quicklook(
                data,
                tlims=(
                    default_config['data_params'][0]['start_time'],
                    default_config['data_params'][0]['end_time'],
                ),
                extra_x=('QDLat', 'MLT', 'Latitude'),
            )
            fig.tight_layout()
            
            # Store in cache
            pn.state.cache[cache_key] = {
                'raw_data': raw_data,
                'data': data,
                'figure': fig,
                'data_view': data._repr_html_(),
                'code_snippet': "# Code snippet will be generated on first use",
                'cli_command': "# CLI command will be generated on first use",
            }
            print("TFA precache completed successfully.")
        except Exception as e:
            print(f"TFA precache failed: {e}")
            import traceback
            traceback.print_exc()

# Run precache at module load time
_populate_tfa_cache()

tfa_gui = TFA_GUI(widgets)

# Create modal for logs
log_modal = pn.Column(
    pn.pane.Markdown("## Messages & Logs"),
    tfa_gui.log_modal,
)

dashboard = pn.template.BootstrapTemplate(
    header=pn.Row(
        HEADER,
        pn.Spacer(),
        tfa_gui.log_button,
        align='center'
    ),
    title="SwarmPAL: TFA",
    sidebar=tfa_gui.sidebar,
    main=tfa_gui.main,
    sidebar_width=380,
    modal=log_modal,
).servable()

# Wire up the log button to open the modal
tfa_gui.log_button.on_click(lambda event: dashboard.open_modal())

if __name__ == '__main__':
    dashboard.show()
