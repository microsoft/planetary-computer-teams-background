# Generate Teams backgrounds from the Planetary Computer

This repository contains a script to generate Teams backgrounds from the Planetary Computer.

__Note__: This project is still under development, and utilizes a unreleased test endpoint from the Planetary Computer. Expect breaking changes until a full release!

## Requirements

- Python >= 3.6

## Usage

To use this project, first install the dependencies.

Ideally in a virtual environment](https://docs.python.org/3/tutorial/venv.html). For example, using a bash shell:

```
> python -m venv venv
> source venv/bin/activate
```

To install dependencies:

```
> pip install -r requirements.txt
```

## Running

Run the script via

```
> python pc_teams_background.py
```

This will generate a new background image based on the settings if it detects that a new image should be generated. A new image is generated if:
- There is no existing background
- If the last image was generated longer than the setting "force_regen_after" ago.
- It detects that the previous background has been used (using the last access time), and the background image does not come from an AOI (described below)
- If the background image is from an AOI, then regenerate if it's been "aois.refresh_days" days from the settings.

A new background image will be selected as follows:
- Construct a set of target

## Settings

You need to edit the settings for your environment. Copy the `settings.template.yaml` file to `settings.yaml`, and edit any required settings.

See the settings file for the meaning of the various settings.

## Image location

The image location is set in the settings file, and should be the folder that Teams saves uploaded images to. This should be something like `c:\Users\${USER}\AppData\Roaming\Microsoft\Teams\Backgrounds\Uploads`.
 If using in WSL 2 (recommended), you can access this through the path `/mnt/c/Users/${USER}/AppData/Roaming/Microsoft/Teams/Backgrounds/Uploads`.

 ## AOIs

 You can provide a GeoJSON feature collection of AOIs that a preferenced for showing by the script. Uncomment the section in the settiongs named `aois` to enable this. You can generate the GeoJSON FeatureCollection however you'd like, but one suggestion is to draw AOIs using the tool [geojson.io](https://geojson.io), and saving off the FeatureCollection that is generated. The script will assign IDs to the features and edit the properties of the collection to keep track of the images used over those areas. If you want to add features to the file, you can copy the contents back into geojson.io, add features, and copy the contents back into the file.

## Setting up a cron job

You can set up a cron job to run this script at a regular interval in order to automatically mix up your Teams background. If you're running Ubuntu in WSL 2, run crontab to create the cron job to activate the Python virtual environment and run the script.

E.g.
```
> crontab -e
(Add this line via the editor:)

*/15 * * * *  /bin/bash -c "cd ~/proj/pc/pc-teams-background && source venv/bin/activate && python pc_teams_background.py"
```

(The above assumes that you cloned the repository to ~/proj/pc-teams-background and followed the above instructions to create a virtualenv)

The above cron job will run the script every 15 minutes.

## Contributing

This project welcomes contributions and suggestions.  Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit https://cla.opensource.microsoft.com.

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
