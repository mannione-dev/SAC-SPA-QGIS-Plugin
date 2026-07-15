import csv
import os
import re
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QDialog, QVBoxLayout, QTextEdit, QPushButton
from qgis.core import Qgis, QgsVectorLayer, QgsFeature, QgsPointXY, QgsGeometry, QgsRectangle, QgsCoordinateReferenceSystem, QgsSpatialIndex, QgsCoordinateTransform, QgsProject
from qgis.gui import QgsMapTool
import requests
import json
from qgis.utils import iface

class SacSpaSpeciesHabitatsPlugin:
    def __init__(self, iface):
        """Initialize the plugin with the QGIS interface."""
        self.iface = iface
        self.action = None
        self.map_tool = None

    def initGui(self):
        """Set up the plugin's GUI elements."""

        plugin_dir = os.path.dirname(__file__)
        icon_path = os.path.join(plugin_dir, "icon.png")

        if not os.path.exists(icon_path):
            print(f"Error: Icon file not found at {icon_path}")
            return

        try:
            self.action = QAction(QIcon(icon_path), "List SAC/SPA...", self.iface.mainWindow())
            self.action.triggered.connect(self.run)
            self.iface.addToolBarIcon(self.action)
            self.iface.addPluginToMenu("&SAC/SPA Tools", self.action)

        except Exception as e:
            print(f"Error creating QAction: {e}")

    def unload(self):
        """Clean up when the plugin is unloaded."""
        self.iface.removePluginMenu("&SAC/SPA Tools", self.action)
        self.iface.removeToolBarIcon(self.action)
        if self.map_tool:
            self.iface.mapCanvas().unsetMapTool(self.map_tool)

    def run(self):
        """Activate the map tool for selecting features."""
        self.map_tool = SacSpaMapTool(self.iface, self.show_results)
        self.iface.mapCanvas().setMapTool(self.map_tool)

    def show_results(self, site_code):
        """Display the species and habitats in a dialog."""

        if not site_code:
            print("Error: No SAC/SPA site selected or invalid site code.")
            return

        # Ensure site code starts with "IE0"
        full_site_code = "IE0" + site_code if not site_code.startswith("IE") else site_code

        species, english_species_names, site_name = self.fetch_species_and_habitats(full_site_code)

        layer = iface.activeLayer()
        if layer:
            layer_id = layer.id()
            print(f"Active layer ID: {layer_id}")
        else:
            print("No active layer.")

        interests_list = scrape_qualifying_interests(site_code, layer_id)

        dialog = ResultsDialog(species, english_species_names, interests_list, site_code, site_name)
        dialog.exec_()

    def fetch_species_and_habitats(self, site_code):
        """Fetch species data for the given site code."""

        if not site_code:
            return [], [], None

        species_data = get_natura2000_data(site_code)
        if not species_data:
            return [], [], None

        species_list, english_species_names, site_name = extract_species_and_site_name(species_data)
        return species_list, english_species_names, site_name

class SacSpaMapTool(QgsMapTool):
    def __init__(self, iface, callback):
        """Initialize the map tool."""
        super().__init__(iface.mapCanvas())
        self.iface = iface
        self.callback = callback
        self.index = None
        self.index_layer = None

    def canvasReleaseEvent(self, event):
        """Handle the mouse click event on the map."""

        layer = self.iface.activeLayer()
        if not isinstance(layer, QgsVectorLayer):
            print("Error: Please select a valid vector layer.")
            return

        geometry_type = layer.geometryType()
        geometry_type_name = {0: "Point", 1: "Line", 2: "Polygon"}.get(geometry_type, "Unknown")

        point = self.toMapCoordinates(event.pos())

        # Transform to layer CRS
        map_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        layer_crs = layer.crs()
        if map_crs != layer_crs:
            transform = QgsCoordinateTransform(map_crs, layer_crs, QgsProject.instance())
            point = transform.transform(point)

        # Check if point is within layer extent
        if not layer.extent().contains(point):
            print("Clicked point is outside the layer's extent.")
            return

        point_geom = QgsGeometry.fromPointXY(point)

        # Determine selection geometry based on layer type
        buffer_distance = 0.0001 if layer_crs.isGeographic() else 50  # degrees or meters
        selection_geom = point_geom.buffer(buffer_distance, 5) if geometry_type in (0, 1) else point_geom

        # Build spatial index
        if self.index is None or self.index_layer != layer:
            self.index = QgsSpatialIndex(layer.getFeatures())
            self.index_layer = layer

        # Find intersecting feature IDs
        intersecting_ids = self.index.intersects(selection_geom.boundingBox())

        # Find features where geometry intersects with selection_geom
        selected_ids = [
            fid for fid in intersecting_ids
            if layer.getFeature(fid).geometry().intersects(selection_geom)
        ]

        if selected_ids:
            layer.selectByIds(selected_ids)
            selected_features = layer.selectedFeatures()
            print(f"Selected {len(selected_features)} features.")

            # Process the first selected feature
            feature = selected_features[0]
            url_field = next(
                (f.name() for f in layer.fields() if f.name().lower() == "url"), None
            )
            if url_field and feature[url_field]:
                site_code = feature[url_field].split("/")[-1].strip("/")
                self.callback(site_code)
            else:
                print("Warning: No 'URL' field or value found in selected feature.")
        else:
            print("No features found at the clicked location.")

        self.iface.mapCanvas().refresh()

# Dialog to display results
class ResultsDialog(QDialog):
    def __init__(self, species, english_species_names, interests_list, site_code, site_name):
        """Initialize the results dialog."""
        super().__init__()
        self.setWindowTitle(f"{site_code} - {site_name}")
        self.setMinimumSize(400, 300)

        layout = QVBoxLayout()

        def add_text_edit(label, data, layout):
            text_edit = QTextEdit()
            text_edit.setReadOnly(True)
            text_edit.setText(label + ("\n".join(data) if data else "No data available."))
            layout.addWidget(text_edit)

        add_text_edit("Species:\n", species, layout)
        add_text_edit("English Species Names:\n", english_species_names, layout)
        add_text_edit("Objectives:\n", interests_list, layout)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button)

        self.setLayout(layout)

# Data fetching functions
def get_natura2000_data(site_code, release_id="55"):
    """Fetch data from the Natura 2000 API."""

    api_url = f"https://n2kbackbone.eea.europa.eu/n2kbackbone_back/api/PublicData/GetReleaseData?sdfPublic=true&siteCode={site_code}&releaseId={release_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    print(f"URL is: {api_url}")

    try:
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print("Error: Could not retrieve species data.")
        return None
    except json.JSONDecodeError as e:
        print("Error: Could not decode API response.")
        return None

def extract_species_and_site_name(data):
    """Extract 'SpeciesName' and 'SiteName' from the API data."""

    species_names = set()
    site_name = None

    if data and "Data" in data:
        ecological_info = data["Data"].get("EcologicalInformation", {})
        for species_key in ["Species", "OtherSpecies"]:
            for species in ecological_info.get(species_key, []):
                species_name = species.get("SpeciesName")
                if species_name:
                    species_names.add(species_name)

        site_info = data["Data"].get("SiteInfo")
        if site_info:
            site_name = site_info.get("SiteName")

    species_list = list(species_names)
    translations = read_species_translations_from_csv()
    english_species_names = [
        translations.get(latin_name, latin_name) for latin_name in species_list
    ]

    return species_list, english_species_names, site_name

def classFactory(iface):
    """Create an instance of the plugin."""
    return SacSpaSpeciesHabitatsPlugin(iface)

def read_species_translations_from_csv():
    """Reads species translations from a CSV file."""

    plugin_dir = os.path.dirname(__file__)
    csv_path = os.path.join(plugin_dir, "data", "species_translations.csv")
    species_translations = {}

    try:
        with open(csv_path, "r", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile)
            for row in reader:
                if len(row) >= 2:
                    latin_name, english_name = row[0].strip(), row[1].strip()
                    species_translations[latin_name] = english_name
    except FileNotFoundError:
        print(f"Error: CSV file not found at {csv_path}")
    except Exception as e:
        print(f"Error: Error reading CSV file: {e}")

    return species_translations

def get_layer_name(layer_id):
    """Gets the layer name from QGIS using the layer ID."""

    project = QgsProject.instance()
    layer = project.mapLayer(layer_id)
    return layer.name() if layer else None

def scrape_qualifying_interests(site_code, layer_id):
    """Scrapes qualifying interests, getting layer name from QGIS."""

    project = QgsProject.instance()
    layer = project.mapLayer(layer_id)

    if not layer:
        return [f"Layer with ID {layer_id} not found."]

    layer_name = layer.name()

    site_type = determine_site_type_from_layer_name(layer_name, layer_id)

    if not site_type:
        return [f"Invalid layer name: {layer_name}."]

    url = f"https://www.npws.ie/protected-sites/{site_type}/{site_code}"

    try:
        print(f"URL: {url}")
        response = requests.get(url)
        response.raise_for_status()
        html_content = response.text

        start_marker = "<h2>Qualifying Interests</h2>"
        start_index = html_content.find(start_marker)

        if start_index == -1:
            return ["Qualifying Interests heading not found."]

        content_start = html_content.find("<", start_index + len(start_marker))
        if content_start == -1:
            return ["Start of qualifying interests content not found."]

        content_end = html_content.find("<h2>", content_start)
        if content_end == -1:
            return ["End of qualifying interests content not found."]

        interests_html = html_content[content_start:content_end]
        interests_text = re.sub(r"<[^>]+>", "", interests_html).strip()
        interests_list = [
            line.strip() for line in interests_text.split('\n') if line.strip()
        ]

        print(f"interests: {interests_list}")
        return interests_list

    except requests.exceptions.RequestException as e:
        return [f"Error fetching URL: {e}"]
    except Exception as e:
        return [f"An unexpected error occurred: {e}"]


def determine_site_type_from_layer_name(layer_name, layer_id):
    """
    Determines the site type (sac or spa) from the layer name.

    Args:
        layer_name (str): The name of the layer.
        layer_id (str): The ID of the layer (for error reporting).

    Returns:
        str: "sac" or "spa" if determined, None otherwise.
    """

    if not layer_name:
        print(f"Error: Invalid layer name, layer ID : {layer_name, layer_id}")
        return None

    # Case-insensitive search for "Special_Area_Conservation" or "Special_Protected_Area"
    if (
        re.search(r"Special_Area_Conservation", layer_name, re.IGNORECASE)
        or re.search(r"SAC_ITM", layer_name, re.IGNORECASE)
        or re.search(r"SAC", layer_name, re.IGNORECASE)
    ):
        return "sac"
    elif (
        re.search(r"Special_Protected_Area", layer_name, re.IGNORECASE)
        or re.search(r"SPA_ITM", layer_name, re.IGNORECASE)
        or re.search(r"SPA", layer_name, re.IGNORECASE)
    ):
        return "spa"
    else:
        print(f"Error: Invalid layer name, layer ID : {layer_name, layer_id}")
        return None  # Or consider raising an exception here

