# This is the script for processing Wadwa2023 data into the common format.
# Author: Samuel Tan, Neo Kok
# Date: 10/24/2024

library(tidyverse)
library(lubridate)


# The dataset is downloaded as a folder containing multiple data tables and forms.
# First, download the entire dataset. Do not rename the downloaded folder.

# Data folder is located in RawData/Wadwa2023/
# Differences in folder name due to future releases should be accounted for:
main_folder = "RawData/Testing_Data/Wadwa2023"
data_folder = list.dirs(main_folder, full.names = TRUE, recursive = FALSE)
data_folder = data_folder[grepl("PEDAP Public Dataset - Release", data_folder)]

# Read in necessary data
cgmData <- read.table(paste0(data_folder, "/Data Files/PEDAPDexcomClarityCGM.txt"), sep = "|", header = TRUE)
demoData <- read.table(paste0(data_folder, "/Data Files/PEDAPDiabScreening.txt"), sep = "|", header = TRUE)
ageData <- read.table(paste0(data_folder, "/Data Files/PtRoster.txt"), sep = "|", header = TRUE)

# Merge demographic data with CGM data
merged_data <- cgmData %>%
  left_join(demoData, by = "PtID")

merged_data <- merged_data %>%
  left_join(ageData, by = "PtID")

# Time processing function for unique data quirk at midnight
add_time_if_missing <- function(x) {
  if (grepl("^\\d{1,2}/\\d{1,2}/\\d{4}$", x)) {
    return(paste(x, "12:00:00 AM"))
  } else {
    return(x)
  }
}

merged_data$DeviceDtTm <- sapply(merged_data$DeviceDtTm, add_time_if_missing)

# Add additional variables: specify the dataset, subject type, device used, and placeholder values
final_data <- merged_data %>%
  mutate(
    id = PtID,
    # Parse in UTC but output as *naive* (no Z / no +00:00) later
    time = mdy_hms(DeviceDtTm, tz = "UTC"),
    gl = as.numeric(CGM),
    age = as.numeric(AgeAsofEnrollDt),
    sex = Sex,
    insulinModality = as.numeric(1),
    type = as.numeric(1),
    device = "Dexcom G6",
    dataset = "wadwa2023"
  ) %>%
  # Remove NA times and gl values
  filter(!is.na(time), !is.na(gl)) %>%
  group_by(id) %>%
  # Ensure that time is in order
  arrange(time) %>%
  # Ungroup the dataset
  ungroup() %>%
  # Write timestamps without any timezone suffix
  # (keeps the moment in UTC but drops the offset/Z in the string)
  mutate(time = format(time, "%Y-%m-%d %H:%M:%S")) %>%
  # Select necessary variables
  select(id, time, gl, age, sex, insulinModality, type, device, dataset)

# Check if 'csv_data' folder exists, create if not
if (!dir.exists("csv_data")) {
  dir.create("csv_data")
}

# Save the processed dataset to a CSV file in the 'csv_data' folder
write_csv(final_data, "csv_data/wadwa2023.csv")
