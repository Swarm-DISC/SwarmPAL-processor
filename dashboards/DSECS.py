import datetime as dt

import matplotlib.pyplot as plt
import panel as pn
import xarray as xr
from xarray import open_datatree

from swarmpal.io import PalDataItem, create_paldata
from swarmpal.toolboxes import dsecs
from swarmpal.experimental import dsecs_plotting
from swarmpal.utils.configs import SPACECRAFT_TO_MAGLR_DATASET

from common import HEADER, JINJA2_ENVIRONMENT, CustomisedFileDropper

pn.extension('filedropper')
xr.set_options(display_expand_groups=True, display_expand_attrs=True, display_expand_data_vars=True, display_expand_coords=True)

DSECS_CODE_TEMPLATE = "dsecs.jinja2"

start_of_today = dt.datetime.now().date()
end_of_today = start_of_today + dt.timedelta(days=1)
four_weeks_ago = end_of_today - dt.timedelta(days=28)
default_start = dt.datetime(2016, 3, 18, 11, 0, 0)
default_end = dt.datetime(2016, 3, 18, 13, 0, 0)  # Reduced to 2 hours for faster demo

widgets = {
    "start-end": pn.widgets.DatetimeRangePicker(
        start=dt.date(2000, 1, 1),
        end=end_of_today,
        value=(default_start, default_end),
        enable_time=False,
        disabled=True  # Disable time selection
    ),
    "netcdf-dropper": CustomisedFileDropper(multiple=False),
    "button-fetch-data": pn.widgets.Button(name="Fetch data", button_type="primary", disabled=True),
    "button-load-netcdf": pn.widgets.Button(name="Load NetCDF", button_type="success"),
    "button-run-preprocess": pn.widgets.Button(name="Run preprocessing", button_type="primary", disabled=True),
    "button-run-analysis": pn.widgets.Button(name="Run DSECS analysis", button_type="primary", disabled=True),
}


class DsecsDataExplorer:
    def __init__(self, widgets):
        self.widgets = widgets
        self.cdf_download = pn.widgets.FileDownload(button_type="success")
        self.interactive_output = pn.pane.HoloViews()
        self.swarmpal_animated = pn.pane.Matplotlib(
            sizing_mode='stretch_width',  # Stretch to fit available width
            max_width=1000,
            min_height=600,  # Ensure minimum height to prevent clipping
            aspect_ratio=None  # Don't constrain aspect ratio
        )  # For animated quicklook
        self.code_snippet = pn.pane.Markdown(styles={"font-size": "15px"})
        self.data_view = pn.pane.HTML()
        self.output_title = pn.pane.Markdown(styles={"font-size": "20px"})
        self.status_info = pn.pane.Markdown("Ready to fetch data...")
        
        # Animation controls for the animated quicklook
        self.frame_slider = pn.widgets.IntSlider(name="Frame", start=0, end=0, value=0, disabled=True)
        self.play_button = pn.widgets.Button(name="▶ Play", button_type="primary", disabled=True)
        self.animation_controls = pn.Row(self.play_button, self.frame_slider)
        
        # Store figures for animation
        self.animation_figures = {}
        self.animation_playing = False
        self._animation_callback = None
        
        self.output_pane = pn.Column(
            self.output_title,
            self.status_info,
            pn.pane.Markdown(
                "💡 **Tips:** \n"
                "• Use 2-hour or 6-hour presets for interactive exploration\n"
                "• Upload pre-computed NetCDF files for instant visualization\n"
                "• Save results to NetCDF for later analysis of longer periods",
                styles={"background-color": "#e7f3ff", "padding": "10px", "border-radius": "5px"}
            ),
            pn.layout.Divider(),
            pn.Tabs(
                ("Data view", self.data_view),
                ("Quicklook", pn.Column(self.animation_controls, self.swarmpal_animated)),
                ("Code snippet", self.code_snippet),
            ),
        )
        
        # Set up event handlers
        # self.widgets["button-fetch-data"].on_click(self.update_input_data)  # Disabled
        self.widgets["button-load-netcdf"].on_click(self.load_netcdf_data)
        # self.widgets["button-run-preprocess"].on_click(self.run_preprocessing)  # Disabled
        # self.widgets["button-run-analysis"].on_click(self.run_analysis)  # Disabled
        
        # Animation control handlers
        self.frame_slider.param.watch(self._update_animation_frame, "value")
        self.play_button.on_click(self._toggle_animation)
        
        # Initialize state
        self.data = None
        self.preprocessed = False
        self.analyzed = False

    @property
    def controls(self):
        data_widgets = pn.Column(
            pn.pane.Markdown("**VirES Data Fetching** *(Disabled)*", styles={"color": "#999"}),
            pn.pane.Markdown("Time range selection:", styles={"color": "#999"}),
            self.widgets["start-end"],
            pn.layout.Divider(),
            self.widgets["button-fetch-data"],
        )
        
        netcdf_widgets = pn.Column(
            pn.pane.Markdown("**Load Pre-computed Results**"),
            pn.pane.Markdown("Upload a NetCDF file from previous DSECS analysis (`.nc`, `.netcdf`, `.nc4`, `.h5`):"),
            self.widgets["netcdf-dropper"],
            self.widgets["button-load-netcdf"],
            pn.pane.Markdown("💡 *Upload your DSECS NetCDF results for instant visualization*", 
                           styles={"font-style": "italic", "color": "#666"})
        )
        
        processing_widgets = pn.Column(
            pn.pane.Markdown("**DSECS Processing** *(Disabled)*", styles={"color": "#999"}),
            self.widgets["button-run-preprocess"],
            self.widgets["button-run-analysis"],
        )
        
        return pn.Column(
            netcdf_widgets,
            pn.layout.Divider(),
            pn.Accordion(
                ("VirES Data (Disabled)", data_widgets),
                ("DSECS Processing (Disabled)", processing_widgets),
                active=[],  # Keep both collapsed by default
                toggle=True
            )
        )

    @property
    def time_start_end_str(self):
        t_s, t_e = self.widgets["start-end"].value
        return f"{t_s.strftime('%Y%m%dT%H%M%S')}_{t_e.strftime('%Y%m%dT%H%M%S')}"

    def get_data_config(self):
        """Parameters to pass to swarmpal to fetch the inputs for both Swarm-A and Swarm-C"""
        # DSECS requires both Swarm-A and Swarm-C data
        spacecraft = ["Swarm-A", "Swarm-C"]
        collections = [SPACECRAFT_TO_MAGLR_DATASET.get(sc) for sc in spacecraft]
        
        data_config = {}
        for collection in collections:
            data_config[collection] = dict(
                collection=collection,
                measurements=["B_NEC"],
                models=["Model = CHAOS"],  # Use "Model = CHAOS" format from notebook
                auxiliaries=["QDLat"],  # Include QDLat as in the notebook
                start_time=self.widgets["start-end"].value[0].isoformat(),
                end_time=self.widgets["start-end"].value[1].isoformat(),
                filters=["OrbitDirection == 1"],  # Ascending passes as in notebook
                server_url="https://vires.services/ows",
                options=dict(asynchronous=False, show_progress=False),
            )
        return data_config

    def fetch_data(self):
        """Fetch data from VirES"""
        # Fetch from VirES
        data = create_paldata(**{
            label: PalDataItem.from_vires(**data_params)
            for label, data_params in self.get_data_config().items()
        })
        self.status_info.object = "Fetched data from VirES"
        print(f"DEBUG: Fetched VirES data. Type: {type(data)}")
        print(f"DEBUG: VirES data groups: {data.groups if hasattr(data, 'groups') else 'No groups attribute'}")
        print(f"DEBUG: VirES data string repr length: {len(str(data))}")
        return data

    def load_netcdf_data(self, event):
        """Load pre-computed DSECS results from NetCDF file"""
        if not self.widgets["netcdf-dropper"].value:
            self.status_info.object = "⚠️ Please upload a NetCDF file first"
            return
            
        try:
            # Load the NetCDF file using SwarmPAL's approach
            file_path = self.widgets["netcdf-dropper"].temp_file.name
            filename = self.widgets["netcdf-dropper"].file_in_mem.name
            self.status_info.object = f"🔄 Loading file: {filename}..."
            
            self.data = open_datatree(file_path, engine='netcdf4')
            
            # Debug: print the actual string representation
            data_str = str(self.data)
            print(f"DEBUG: Data string representation (first 500 chars): {data_str[:500]}")
            
            # Check if this looks like DSECS output
            groups_str = str(self.data.groups) if hasattr(self.data, 'groups') else str(self.data)
            has_dsecs_output = "DSECS_output" in groups_str or "currents" in groups_str
            
            if has_dsecs_output:
                # File already contains DSECS results
                self.preprocessed = True
                self.analyzed = True
                self.status_info.object = "✅ **Pre-computed DSECS results loaded successfully!**"
                
                # Update all displays
                self._update_data_view()
                self._update_quicklook()
                self._update_code_snippet()
                
            else:
                # File contains raw data, still needs processing
                self.preprocessed = False
                self.analyzed = False
                self.status_info.object = "✅ Raw data loaded from NetCDF (ready for processing"
                
                # Update data view only
                self._update_data_view()
                self._update_code_snippet()
                
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"DEBUG: NetCDF loading error: {error_details}")
            self.status_info.object = f"❌ **Failed to load file '{filename}':** {str(e)}<br><details><summary>Click for details</summary><pre>{error_details}</pre></details>"

    def run_preprocessing(self, event):
        """Run DSECS preprocessing step"""
        if self.data is None:
            self.status_info.object = "⚠️ Please fetch data first"
            return
            
        try:
            # Create preprocessing process
            p1 = dsecs.processes.Preprocess()
            # Configure with the dataset names in our data tree
            # Handle both dictionary-like and tuple-like groups
            if hasattr(self.data.groups, 'keys'):
                dataset_names = list(self.data.groups.keys())
            else:
                # If groups is a tuple or list, convert to list
                dataset_names = list(self.data.groups)
            
            # Debug information
            self.status_info.object = f"🔍 Found datasets: {dataset_names}"
            
            alpha_dataset = None
            charlie_dataset = None
            
            # Find Swarm-A and Swarm-C datasets
            for name in dataset_names:
                if "MAGA" in name or "Swarm-A" in name:
                    alpha_dataset = name
                elif "MAGC" in name or "Swarm-C" in name:
                    charlie_dataset = name
            
            if not alpha_dataset or not charlie_dataset:
                self.status_info.object = "⚠️ Could not find both Swarm-A and Swarm-C datasets"
                return
                
            p1.set_config(dataset_alpha=alpha_dataset, dataset_charlie=charlie_dataset)
            self.data = p1(self.data)
            self.preprocessed = True
            self.status_info.object = "✅ Preprocessing completed"
            
            # Update data view
            self._update_data_view()
            self._update_code_snippet()
            
        except Exception as e:
            self.status_info.object = f"❌ Preprocessing failed: {str(e)}"

    def run_analysis(self, event):
        """Run DSECS analysis step"""
        if self.data is None:
            self.status_info.object = "⚠️ Please fetch data first"
            return
            
        if not self.preprocessed:
            self.status_info.object = "⚠️ Please run preprocessing first"
            return
            
        try:
            # Show progress indicator
            start_time = dt.datetime.now()
            self.status_info.object = "🔄 **Running DSECS analysis...** This may take several minutes. Please be patient."
            
            # Create analysis process
            p2 = dsecs.processes.Analysis()
            self.data = p2(self.data)
            self.analyzed = True
            
            # Calculate elapsed time
            elapsed_time = (dt.datetime.now() - start_time).total_seconds()
            self.status_info.object = f"✅ **DSECS analysis completed** in {elapsed_time:.1f} seconds"
            
            # Update visualizations
            self._update_data_view()
            self._update_quicklook()
            self._update_code_snippet()
            
        except Exception as e:
            self.status_info.object = f"❌ **Analysis failed:** {str(e)}"

    def update_input_data(self, event):
        """Fetch and display input data"""
        try:
            self.data = self.fetch_data()
            self.preprocessed = False
            self.analyzed = False
            
            print(f"DEBUG: After fetch, data type: {type(self.data)}")
            print(f"DEBUG: After fetch, data string repr (first 200 chars): {str(self.data)[:200]}")
            
            # Update displays
            self._update_data_view()
            self._update_code_snippet()
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"DEBUG: VirES fetch error: {error_details}")
            self.status_info.object = f"❌ Data fetch failed: {str(e)}"

    def _update_data_view(self):
        """Update the data view pane"""
        if self.data is not None:
            try:
                # Use the same approach as FAC.py - _repr_html_() provides rich HTML representation
                self.data_view.object = self.data._repr_html_()
            except Exception as e:
                # Fallback to string representation if _repr_html_() fails
                try:
                    raw_string = str(self.data)
                    self.data_view.object = f"<pre style='white-space: pre-wrap; font-family: monospace; font-size: 12px; max-height: 400px; overflow-y: auto;'>{raw_string}</pre>"
                except Exception as e2:
                    # Show error with data type information
                    error_info = f"❌ Error displaying data: {str(e)}\n"
                    error_info += f"Fallback error: {str(e2)}\n"
                    error_info += f"Data type: {type(self.data)}\n"
                    if hasattr(self.data, '__dict__'):
                        error_info += f"Attributes: {list(self.data.__dict__.keys())}"
                    self.data_view.object = f"<pre>{error_info}</pre>"
        else:
            self.data_view.object = "<p>No data loaded</p>"

    def _update_quicklook(self):
        """Update the quicklook visualization"""
        if self.data is not None and self.analyzed:
            try:
                # Use the experimental plotting functions
                figs = dsecs_plotting.quicklook(self.data)
                if figs:
                    # Setup animated quicklook
                    self._setup_animated_quicklook(figs)
                else:
                    self.status_info.object = "⚠️ No figures generated from DSECS quicklook"
            except Exception as e:
                self.status_info.object = f"⚠️ Plotting error: {str(e)}"

    def _setup_animated_quicklook(self, figures_dict):
        """Setup the animated quicklook with frame controls"""
        # Resize figures to fit better on screen
        resized_figures = {}
        for frame_id, fig in figures_dict.items():
            try:
                # Adjust the figure size for better screen fit
                fig.set_size_inches(12, 8)  # Smaller than default, good for dashboards
                fig.tight_layout()
                resized_figures[frame_id] = fig
                
            except Exception as e:
                print(f"Warning: Could not resize figure {frame_id}: {e}")
                # Fall back to original figure if resizing fails
                resized_figures[frame_id] = fig
        
        self.animation_figures = resized_figures
        
        if resized_figures:
            frames = list(resized_figures.keys())
            if frames:
                # Enable and configure the slider
                self.frame_slider.param.update(
                    start=min(frames), 
                    end=max(frames), 
                    value=min(frames),
                    disabled=False
                )
                self.play_button.disabled = False
                
                # Display the first frame
                self.swarmpal_animated.object = resized_figures[min(frames)]
            else:
                self._disable_animation_controls()
        else:
            self._disable_animation_controls()

    def _disable_animation_controls(self):
        """Disable animation controls when no data available"""
        self.frame_slider.disabled = True
        self.play_button.disabled = True
        self.swarmpal_animated.object = self._pending_matplotlib_figure()

    def _update_animation_frame(self, event):
        """Update the displayed frame when slider changes"""
        frame = event.new
        if frame in self.animation_figures:
            self.swarmpal_animated.object = self.animation_figures[frame]

    def _toggle_animation(self, event):
        """Toggle animation play/pause"""
        if self.animation_playing:
            # Stop animation
            self.animation_playing = False
            self.play_button.name = "▶ Play"
            if hasattr(self, '_animation_callback') and self._animation_callback:
                self._animation_callback.stop()
                self._animation_callback = None
        else:
            # Start animation
            self.animation_playing = True
            self.play_button.name = "⏸ Pause"
            self._start_animation()

    def _start_animation(self):
        """Start the animation loop"""
        frames = list(self.animation_figures.keys())
        if not frames:
            return
            
        def advance_frame():
            if not self.animation_playing:
                return
                
            current_frame = self.frame_slider.value
            current_idx = frames.index(current_frame) if current_frame in frames else 0
            next_idx = (current_idx + 1) % len(frames)
            next_frame = frames[next_idx]
            
            self.frame_slider.value = next_frame
        
        # Update every 1.2 seconds (matching the original interval)
        self._animation_callback = pn.state.add_periodic_callback(advance_frame, period=1200)

    def _update_code_snippet(self):
        """Update the code snippet display"""
        try:
            code = self.get_code()
            self.code_snippet.object = f"```python\n{code}\n```"
        except Exception:
            self.code_snippet.object = "Code snippet generation failed"

    def get_code(self):
        """Generate Python code snippet"""
        template = JINJA2_ENVIRONMENT.get_template(DSECS_CODE_TEMPLATE)
        
        return template.render(
            mode="vires",
            start_time=self.widgets["start-end"].value[0].isoformat(),
            end_time=self.widgets["start-end"].value[1].isoformat(),
            preprocessed=self.preprocessed,
            analyzed=self.analyzed,
        )

    @staticmethod
    def _pending_matplotlib_figure():
        """Create a placeholder figure"""
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.text(0.5, 0.5, "Processing...", ha='center', va='center', 
                fontsize=16, transform=ax.transAxes)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis('off')
        return fig


# Create the explorer instance
data_explorer = DsecsDataExplorer(widgets)

# Define the layout
template = pn.template.BootstrapTemplate(
    title="DSECS: Dipolar Spherical Elementary Current Systems",
    header=HEADER,
    sidebar=[data_explorer.controls],
    main=[data_explorer.output_pane],
)

# Serve the template
template.servable()

if __name__ == "__main__":
    template.show()
