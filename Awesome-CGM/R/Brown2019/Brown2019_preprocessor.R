# Author: Samuel Tan, Neo Kok
# Date: 10/24/2024

library(tidyverse)
library(haven)
library(lubridate)

# The dataset is downloaded as a folder containing multiple data tables and forms.
# First, download the entire dataset. Do not rename the downloaded folder.

# Data folder is named "DCLP3 Public Dataset - Release 3 - 2022-08-04" - rename if downloaded file name changes

# Set the path to the Brown2019 data folder
data_folder = "RawData/Testing_Data/Brown2019/DCLP3 Public Dataset - Release 3 - 2022-08-04"

# Read in necessary data
cgmData <- read.table(paste0(data_folder, "/Data Files/cgm.txt"), sep = "|", header = TRUE)
screeningData <- read.table(paste0(data_folder, "/Data Files/DiabScreening_a.txt"),
                            sep = "|", header = TRUE, fill = TRUE, fileEncoding = "UTF-16LE", stringsAsFactors = FALSE, na.strings = "")

# Merge demographic data with CGM data
merged_data <- cgmData %>%
  left_join(screeningData, by = "PtID")

# Add additional variables: specify the dataset, subject type, device used, and placeholder values
final_data <- merged_data %>%
  mutate(
    id = PtID,
    time = as.POSIXct(dmy_hms(DataDtTm), format = "%Y-%m-%d %H:%M:%S"),
    gl = as.numeric(CGM),
    age = AgeAtEnrollment,
    sex = Gender,
    insulinModality = as.numeric(1),
    type = as.numeric(1),
    device = "Dexcom G6",
    dataset = "o_malley2021"
  ) %>%
  # Remove NA times and gl values
  filter(!is.na(time), !is.na(gl)) %>%
  group_by(id) %>%
  # Ensure that time is in order
  arrange(time) %>%
  # Ungroup the dataset after ordering by time
  ungroup() %>%
  # Select necessary variables (preserve original subject id)
  select(id, time, gl, age, sex, insulinModality, type, device, dataset)

# Check if 'csv_data' folder exists, create if not
if (!dir.exists("csv_data")) {
  dir.create("csv_data")
}

# Save the processed dataset to a CSV file in the 'csv_data' folder
final_data %>% write.csv("csv_data/o_malley2021.csv", row.names = FALSE)

