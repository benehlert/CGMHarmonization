# This is the script for processing Tsalikian2005 data into the common format.
# Author: David Buchanan
# Date: January 31st, 2020; edited June 13th, 2020 by Elizabeth Chun and June 7th, 2024 by Walter Williamson

library(dplyr)

# This study downloads as a folder containing data tables and forms
# First download the entire dataset. Do not rename the downloaded folder
# Place the downloaded folder into a folder of your creation specific for this dataset
# You may name your created folder however you like
# Here we have named the created folder by first author last name and date of the original paper
dataset <- "Tsalikian2005"
# If you have a different naming method, you will need to adjust this, eg.
# dataset <- "insert_your_name"

# We will use the RawData directory structure
# The working directory remains as CGMHarmonization

# This is the relative path to the CGM and study site files with original names
file.path <- paste("RawData/Testing_Data", dataset, "DirecNetInPtExercise", "DataTables", "tblDDataCGMS.csv", sep = "/")
file.path.2 <- paste("RawData/Testing_Data", dataset, "DirecNetInPtExercise", "DataTables", "tblDPtRoster.csv", sep = "/")
# Alternatively, if the file structure has been changed, simply place the tblDDataCGMS.csv file into the created folder
# Then run the file paths as follows:
# file.path <- "tblDDataCGMS.csv"
# file.path.2 <- "tblDPtRoster.csv"

# Read the tables in
curr = read.csv(file.path)
sites = read.csv(file.path.2)

#Add siteID and timezone information for each subject to the CGM table
sites = sites[2:3]
common_col = intersect(names(curr), names(sites))
curr = merge(curr, sites, by = common_col)
curr = curr %>% mutate(SiteID =
                         case_when(
                           SiteID == 1 ~ "MST",
                           SiteID == 2 ~ "CST",
                           SiteID == 3 | SiteID == 5 ~ "EST",
                           SiteID == 4 ~ "PST")
)

# Standardize date and time
# Convert the 12-hour time to 24
curr$ReadingTm = strftime(strptime(curr$ReadingTm, "%I:%M %p"), format = "%H:%M:%S")
# Remove the time information from the date
curr$ReadingDt = strftime(curr$ReadingDt, format = "%Y-%m-%d")
# Combine into a standardized string (no timezone; no shifting)
curr$time = paste(curr$ReadingDt, curr$ReadingTm)

# No timezone assignment; just sort within subject by the naive clock time
curr = curr %>%
  group_by(PtID) %>%
  arrange(time) %>%
  ungroup()

# Reorder/rename and keep only the columns we want
curr = curr[c(1,8,6)]

# Renaming the columns the standard format names
colnames(curr) = c("id","time","gl")

# Ensure glucose values are numeric
curr$gl = as.numeric(curr$gl)
# time is already a standardized "YYYY-MM-DD HH:MM:SS" string

# Save the cleaned data to the current directory (CGMHarmonization)
# The cleaned file will be named "dataset"_processed.csv
write.table(curr, file = paste(dataset, "_processed.csv", sep = ""),
            row.names=F, col.names = !file.exists(paste(dataset, "_processed.csv", sep = "")),
            append = T, sep = ",")
