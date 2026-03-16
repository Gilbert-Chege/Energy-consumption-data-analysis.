import sys

import sqlite3
import re
from datetime import datetime
import pandas as pd

from PyQt6.QtWidgets import (
    QApplication, QWidget, QPushButton, QFileDialog,
    QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QMessageBox, QInputDialog
)


class ElectricityApp(QWidget):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("KPLC Electricity Usage Analyzer")
        self.resize(1200, 600)  # wider for extra columns

        self.file_label = QLabel("No file  selected")
        self.client_label = QLabel("No client selected")

        # Buttons
        browse_btn = QPushButton("Browse file ")
        browse_btn.clicked.connect(self.browse_file)

        process_btn = QPushButton("Extract Tokens")
        process_btn.clicked.connect(self.process_file)

        monthly_btn = QPushButton("Show Monthly Usage")
        monthly_btn.clicked.connect(self.show_monthly)

        export_btn = QPushButton("Export Processed Data")
        export_btn.clicked.connect(self.export_csv)

        self.table = QTableWidget()

        # Layout
        layout = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(browse_btn)
        top.addWidget(process_btn)
        top.addWidget(monthly_btn)
        top.addWidget(export_btn)
        layout.addLayout(top)
        layout.addWidget(self.file_label)
        layout.addWidget(self.client_label)
        layout.addWidget(self.table)
        self.setLayout(layout)

        # State variables
        self.file_path = None
        self.client_name = None
        self.db_name = None
        self.current_df = None  # last displayed data

    def browse_file(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "Select file  file", "", " Files (*.)"
        )
        if file:
            self.file_path = file
            self.file_label.setText(file)
            
    def ask_client(self):
        client_name, ok = QInputDialog.getText(self, "Client Name", "Enter client name:")
        if not ok or not client_name.strip():
            QMessageBox.warning(self, "Error", "Client name is required")
            return False
        self.client_name = client_name.strip()
        self.client_label.setText(f"Client: {self.client_name}")
        self.db_name = f"electricity_tokens_{self.client_name}.db"
        return True

    def process_file(self):
        if not self.file_path:
            QMessageBox.warning(self, "Error", "Select a  file first")
            return
        if not self.client_name:
            if not self.ask_client():
                return

        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()

        # Updated table with Amt, TknAmt, OtherCharges
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            date TEXT,
            units REAL,
            meter_number TEXT,
            amt REAL,
            tkn_amt REAL,
            other_charges REAL,
            message TEXT
        )
        """)
        conn.commit()

        tree = ET.parse(self.file_path)
        root = tree.getroot()
        records = []

        for sms in root.findall("sms"):
            sender = sms.attrib.get("address", "")
            body = sms.attrib.get("body", "")
            timestamp = sms.attrib.get("date", "0")

            # Only Kenya Power messages
            if "POWER" not in sender.upper():
                continue

            # Extract Units
            units_match = re.search(r"Units[:\s]*([0-9.]+)", body, re.IGNORECASE)
            # Extract Meter Number
            meter_match = re.search(r"Mtr[:\s]*([0-9]+)", body, re.IGNORECASE)
            # Extract Amt, TknAmt, OtherCharges
            amt_match = re.search(r"Amt[:\s]*([0-9.]+)", body, re.IGNORECASE)
            tknamt_match = re.search(r"TknAmt[:\s]*([0-9.]+)", body, re.IGNORECASE)
            other_match = re.search(r"OtherCharges[:\s]*([0-9.]+)", body, re.IGNORECASE)

            if units_match:
                units = float(units_match.group(1))
                meter_number = meter_match.group(1) if meter_match else "Unknown"
                amt = float(amt_match.group(1)) if amt_match else 0.0
                tkn_amt = float(tknamt_match.group(1)) if tknamt_match else 0.0
                other_charges = float(other_match.group(1)) if other_match else 0.0
                date = datetime.fromtimestamp(int(timestamp) / 1000)

                # Insert into database
                cursor.execute(
                    "INSERT INTO tokens (sender,date,units,meter_number,amt,tkn_amt,other_charges,message) VALUES (?,?,?,?,?,?,?,?)",
                    (sender, date.strftime("%Y-%m-%d"), units, meter_number, amt, tkn_amt, other_charges, body)
                )

                records.append([sender, meter_number, date.strftime("%Y-%m-%d"), units, amt, tkn_amt, other_charges])

        conn.commit()
        conn.close()

        if not records:
            QMessageBox.information(self, "Info", "No KPLC messages found.")
            return

        # Define DataFrame with new columns
        df = pd.DataFrame(records, columns=[
            "Sender", "Meter Number", "Date", "Units", "Amt", "TknAmt", "OtherCharges"
        ])
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.sort_values(['Meter Number', 'Date'])

        # Days between tokens per meter
        df['Days Between Tokens'] = df.groupby('Meter Number')['Date'].diff().dt.days
        # Daily usage kWh/day
        df['Daily Usage (kWh/day)'] = df['Units'] / df['Days Between Tokens']

        # Reorder columns
        df = df[
            ["Sender", "Meter Number", "Date", "Units", "Amt", "TknAmt", "OtherCharges",
             "Days Between Tokens", "Daily Usage (kWh/day)"]
        ]

        self.current_df = df
        self.display_table(df)

    def show_monthly(self):
        if not self.client_name:
            if not self.ask_client():
                return

        conn = sqlite3.connect(self.db_name)
        query = """
        SELECT 
        strftime('%Y-%m',date) as Month,
        SUM(units) as Monthly_Units,
        SUM(amt) as Monthly_Amt,
        SUM(tkn_amt) as Monthly_TknAmt,
        SUM(other_charges) as Monthly_OtherCharges
        FROM tokens
        GROUP BY Month
        ORDER BY Month
        """
        df = pd.read_sql_query(query, conn)
        conn.close()

        if df.empty:
            QMessageBox.information(self, "Info", "No data processed yet.")
            return

        self.current_df = df
        self.display_table(df)

    def export_csv(self):
        if self.current_df is None or self.current_df.empty:
            QMessageBox.warning(self, "Error", "No data to export.")
            return

        file, _ = QFileDialog.getSaveFileName(self, "Save CSV", "", "CSV Files (*.csv)")
        if file:
            self.current_df.to_csv(file, index=False)
            QMessageBox.information(self, "Success", f"Data exported to {file}")

    def display_table(self, df):
        self.table.setRowCount(len(df))
        self.table.setColumnCount(len(df.columns))
        self.table.setHorizontalHeaderLabels(df.columns)

        for i in range(len(df)):
            for j in range(len(df.columns)):
                value = df.iloc[i, j]
                if pd.isna(value):
                    value = ""
                self.table.setItem(i, j, QTableWidgetItem(str(value)))

import xml.etree.ElementTree as ET
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ElectricityApp()
    window.show()
    sys.exit(app.exec())