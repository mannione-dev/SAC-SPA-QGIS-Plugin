from qgis.PyQt import uic
import os

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'sac_spa_species_habitats_dialog.ui'))

class SacSpaSpeciesHabitatsDialog(FORM_CLASS):
    def __init__(self, parent=None):
        super(SacSpaSpeciesHabitatsDialog, self).__init__(parent)
        self.setupUi(self)
