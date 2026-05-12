# This is the script for processing Colas2019 data into the common format.
# Author: Elizabeth Chun
# Date: 1/30/23, edited 10/24/24 by Neo Kok
library(tidyverse)
library(hms)

# The dataset is downloaded as a folder containing multiple data tables and forms.
# First, download the entire dataset. Do not rename the downloaded folder.

# Data folder is named "S1" - rename if downloaded file name changes

# This study downloads with a csv file for each subject
dataset <- "RawData/Training_Data/Colas2019/S1"

# Here we list out the csv files within the "S1" folder
files = list.files(path = dataset, pattern = "csv")

# Initialize list to store dataframes
results <- list()

# Next we loop through each file
nfiles = length(files)
for (i in 1:nfiles){

  # Track progress
  # print(files[i])
  # print(str_split_1(str_split_1(files[i], " +")[2], "\\.")[1])

  # Read each csv in from the "S1" folder
  curr = read.csv(file.path(dataset, files[i]))
  curr = curr %>%
    mutate(
      # Extract id from csv filename
      id = str_split_1(str_split_1(files[i], " +")[2], "\\.")[1]
    ) %>%
    select(id, time = hora, gl = glucemia) %>%
    mutate(
      id = as.numeric(id),
      time = as_hms(time),
      # Work in seconds to preserve hh:mm:ss
      timediffs = c(0, as.numeric(diff(time, units = "seconds"))),
      timediffs_adj = dplyr::if_else(timediffs < 0, 24*60*60 + timediffs, timediffs)
    )

  # Base date chosen as 1970-01-01
  base_date <- as.POSIXct("1970-01-01 00:00:00", format="%Y-%m-%d %H:%M:%S")
  # Preserve starting seconds and accumulate in seconds
  start_sec <- as.numeric(curr$time[1])
  cu_sec <- cumsum(curr$timediffs_adj) + start_sec
  curr$time <- base_date + lubridate::seconds(cu_sec)

  curr <- curr %>%
    select(id, time, gl) %>%
    filter(!is.na(gl))

  # Store the dataframe in the list
  results[[i]] <- curr
}

# Combine all dataframes into one
data <- bind_rows(results)


# Read in data
demo <- read.table("RawData/Training_Data/Colas2019/S1/clinical_data.txt", sep = "", header = TRUE) %>% select(age, sex = gender, T2DM)

# Add id based on column row
demo = rowid_to_column(demo, "id")

# Merge data
df_merged = left_join(data, demo, by = "id")


# Finalize data
df_final = df_merged %>% mutate(time = as.POSIXct(time, format = "%Y-%m-%d %H:%M:%S"), # Ensure correct time format
                               # Set sex to M if 0 and F if 1
                               sex = ifelse(sex == 0, "M", "F"),
                               # Set insulin modality to NA as we don't have that information, set as numeric to keep consistent with other datasets
                               insulinModality = NA_integer_,
                               # Set type to 0 if not diabetic and 2 if T2d
                               type = as.numeric(ifelse(T2DM, 2, 0)),
                               # Set device type to Medtronic iPro for all subjects
                               device = "Medtronic iPro",
                               # Set dataset type to be Lynch2022 for future reference when combined
                               dataset = "colas2019") %>%
  # Remove NA times and gl values
  filter(!is.na(time), !is.na(gl)) %>%
  group_by(id) %>%
  # Ensure that time is in order
  arrange(time) %>%
  # Ungroup the dataset
  ungroup() %>%
  select(id, time, gl, age, sex, insulinModality, type, device, dataset) # Reorder columns and select only the relevant ones for the output


# Check if 'csv_data' folder exists, create if not
if (!dir.exists("csv_data")) {
  dir.create("csv_data")
}

write.csv(df_final, "csv_data/colas2019.csv", row.names = FALSE)
