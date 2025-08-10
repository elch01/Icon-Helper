# Icon-Helper
Python utility desgined to help create icon themes on Linux

<img width="1288" height="917" alt="image" src="https://github.com/user-attachments/assets/2ea00c90-b8dc-4c0b-b8ab-97c9e58ee056" />


## Summary

This is like super early in development, but it fits my usecase (Mint-X) for the moment.

- View the entire icon theme from a easy grid view.
- Edit icons seamlessly by just clicking on the icon you want to add or adjust and bitmaps will be generated for the 16, 22, 24, 32 and 48 folders.
- Easy creation of symlinks, always relative!
- A good base index of icons to read from ```icon_categories.json``` but can be updated using json_generator (more information further down)
- Toggle hiding symlinks and symbolic icons

## Pre-requisities

Below are the packages required to run this script
```
sudo apt install python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 inkscape
```

## How to use

- Download the entire repo by pressing the green ```Code``` button at the top and Download the zip file. (or use git clone)
- Install the Pre-requisities and run the script ```./IconHelper.py```
- Press the "Choose Theme Folder" button and select your theme folder (Where the theme.index file is located).
- Go wild, break the script, create icons with all your heart!

## Tools

### json-generator.py
This tool is used to generate the ```icon_categories.json``` file which is used as a kind of index for all the icons, if your theme is missing an icon that is in the index a placeholder "Missing Icon" is placed instead.

It "merges" two themes, such as Mint-X and Mint-Y and creates a full index of all the icons in both of them for the Theme Helper to read from.

How to use:

```./json-generator -t ~/.icons/Mint-Y/ -m ~/.icons/Mint-X -o /tmp/icon_categories.json```\


```
"-t" is used as the base theme

"-m" is used as the theme that is going to merge into the base theme

"-o" is the output of the merged indexes
```
