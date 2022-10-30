# Strava-to-FitTrackee

This is a little tool that will pull workouts from a [Strava](https://www.strava.com/)
account and push them to a [FitTrackee](https://github.com/SamR1/FitTrackee/) instance.
The tool was written to help automatically backup workout tracks from the commercial
service onto a self-hosted instance for safe keeping.

## Prerequisites

You'll need the following:

  - An installation of [FitTrackee](https://github.com/SamR1/FitTrackee/)
  - An OAuth2 application configured for FitTrackee (go to the "Apps" section
    of your FitTrackee account to configure this)
  - A Strava account with an API token enabled (see 
    [API Settings](https://www.strava.com/settings/api))
  - [Poetry](https://python-poetry.org/) installed on the system where this
    tool will run

## Installation

Download or clone the code from this repository:

```sh
$ git clone https://github.com/jat255/strava-to-fittrackee.git
```

Copy the `.env.example` file to `.env` and configure the values as 
documented in order to set the appropriate application URLs and API keys