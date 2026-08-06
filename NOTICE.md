# FrameMeld notices

FrameMeld is licensed under the GNU General Public License version 3 only
(`GPL-3.0-only`). See `LICENSE` for the complete terms.

## Blur reference

FrameMeld's frame-processing behavior was developed from and compared against
the GPLv3-licensed Blur project by f0e. The fixed reference revision is:

- Project: <https://github.com/f0e/blur>
- Revision: `6fd0eccf7bf1c142a80473a8b0b937558e498975`
- Source: <https://github.com/f0e/blur/tree/6fd0eccf7bf1c142a80473a8b0b937558e498975>

The `blur-master` development checkout is not required to build FrameMeld and
is intentionally not included in this repository. FrameMeld replaces Blur's
GUI, CLI, configuration system, and monolithic VapourSynth script with its own
headless launcher, command protocol, automatic frame-rate policy, and modular
processing engine. This notice does not imply endorsement by the Blur authors.

## Bundled components

The Windows build script downloads pinned releases of FFmpeg, Python,
VapourSynth, RIFE NCNN Vulkan, Akarin, BestSource, L-SMASH Works, MVTools,
SVPFlow, and Vapoursynth-adjust. Exact source URLs, versions, and SHA-256
digests are recorded in `config/windows-runtime.json`.

Those components retain their own copyrights and license terms. A generated
runtime preserves upstream notices from the downloaded packages and includes
FrameMeld's GPLv3 license and this notice. Redistributors are responsible for
meeting the source-availability and notice requirements of every bundled
component.
