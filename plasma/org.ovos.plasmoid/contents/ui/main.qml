import QtQuick 2.15
import QtQuick.Layouts 1.15
import org.kde.plasma.plasmoid 2.0
import org.kde.plasma.core as PlasmaCore
import org.kde.kirigami 2.20 as Kirigami

PlasmoidItem {
    id: root

    width: Kirigami.Units.gridUnit * 20
    height: Kirigami.Units.gridUnit * 15
    Layout.minimumWidth: Kirigami.Units.gridUnit * 12
    Layout.minimumHeight: Kirigami.Units.gridUnit * 8

    Plasmoid.backgroundHints: "StandardBackground"
    preferredRepresentation: fullRepresentation

    property string ovosStatus: "idle"
    property string ovosTranscript: ""
    property var ovosWaveform: []

    // Aparlencia configurable (llega desde /state -> config)
    property string waveformStyle: "barras"
    property real waveformOpacity: 1.0
    property color waveformColor: "#ff2200"
    property color statusColorListening: "#00cc44"
    property color statusColorThinking: "#3399ff"
    property color statusColorSpeaking: "#ff2200"
    property color statusColorIdle: "#666666"

    readonly property string backendUrl: "http://127.0.0.1:8765"

    function applyConfig(cfg) {
        if (!cfg || !cfg.gui) return;
        root.statusColorListening = cfg.gui.color_listening || "#00cc44";
        root.statusColorThinking  = cfg.gui.color_thinking  || "#3399ff";
        root.statusColorSpeaking  = cfg.gui.color_speaking  || "#ff2200";
        root.statusColorIdle      = cfg.gui.color_idle      || "#666666";
        root.waveformColor        = cfg.gui.waveform_color  || "#ff2200";
        root.waveformStyle        = cfg.gui.waveform_style  || "barras";
        root.waveformOpacity      = parseFloat(cfg.gui.waveform_opacity) || 1.0;
    }

    function pollState() {
        var xhr = new XMLHttpRequest();
        xhr.open("GET", backendUrl + "/state", true);
        xhr.onreadystatechange = function() {
            if (xhr.readyState === XMLHttpRequest.DONE) {
                if (xhr.status === 200) {
                    try {
                        var data = JSON.parse(xhr.responseText);
                        root.ovosStatus = data.status || "idle";
                        root.ovosTranscript = data.transcript || "";
                        root.ovosWaveform = data.waveform || [];
                        root.applyConfig(data.config);
                        if (root.waveformStyle === "linea")
                            oscCanvas.requestPaint();
                    } catch (e) {}
                }
            }
        };
        xhr.send();
    }

    Timer {
        interval: 50
        running: true
        repeat: true
        onTriggered: pollState()
    }

    Plasmoid.status: {
        switch (root.ovosStatus) {
            case "listening":
            case "thinking":
            case "speaking":
                return PlasmaCore.Types.NeedsAttentionStatus;
            default:
                return PlasmaCore.Types.ActiveStatus;
        }
    }

    property color statusColor: {
        switch (root.ovosStatus) {
            case "listening": return root.statusColorListening;
            case "thinking": return root.statusColorThinking;
            case "speaking": return root.statusColorSpeaking;
            default: return root.statusColorIdle;
        }
    }

    property string statusLabel: {
        switch (root.ovosStatus) {
            case "listening": return "Escuchando...";
            case "thinking": return "Pensando...";
            case "speaking": return "Hablando";
            default: return "Inactivo";
        }
    }

    fullRepresentation: Item {
        id: mainItem
        anchors.fill: parent
        anchors.margins: Kirigami.Units.smallSpacing

        ColumnLayout {
            anchors.fill: parent
            spacing: Kirigami.Units.smallSpacing

            Item {
                id: waveformContainer
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredHeight: parent.height * 0.55
                clip: true

                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    height: 2
                    color: PlasmaCore.Theme.textColor
                    opacity: 0.2
                    visible: root.waveformStyle !== "circulos"
                }

                Row {
                    id: barsRow
                    anchors.centerIn: parent
                    height: parent.height
                    width: parent.width
                    spacing: Math.max(1, parent.width / 72)
                    visible: root.waveformStyle !== "linea"

                    Repeater {
                        model: 48
                        delegate: Item {
                            required property int index
                            readonly property real wval: {
                                if (root.ovosWaveform && index < root.ovosWaveform.length)
                                    return root.ovosWaveform[index];
                                return 0.0;
                            }
                            readonly property real totalGaps: (48 - 1) * barsRow.spacing
                            readonly property real barWidth: Math.max(2, (barsRow.width - totalGaps) / 48)

                            width: root.waveformStyle === "circulos" ? barWidth * 1.3 : barWidth
                            height: waveformContainer.height * 0.88
                            anchors.verticalCenter: parent.verticalCenter

                            Rectangle {
                                anchors {
                                    left: parent.left
                                    right: parent.right
                                    verticalCenter: parent.verticalCenter
                                }
                                height: parent.height
                                radius: root.waveformStyle === "circulos"
                                        ? Math.max(1, barWidth * 1.3 / 2)
                                        : Math.max(1, barWidth / 3)
                                color: Qt.rgba(root.waveformColor.r, root.waveformColor.g, root.waveformColor.b, 0.1 * root.waveformOpacity)
                            }

                            Rectangle {
                                anchors {
                                    horizontalCenter: parent.horizontalCenter
                                    verticalCenter: parent.verticalCenter
                                }
                                width: root.waveformStyle === "circulos"
                                       ? parent.width * (0.18 + 0.9 * wval)
                                       : parent.width
                                height: root.waveformStyle === "circulos"
                                        ? width
                                        : parent.height * wval
                                radius: root.waveformStyle === "circulos" ? width / 2 : Math.max(1, barWidth / 3)
                                color: root.waveformColor
                                opacity: root.waveformOpacity * (0.85 + 0.15 * wval)

                                Behavior on height {
                                    SpringAnimation {
                                        spring: 4.0
                                        damping: 0.2
                                        mass: 0.6
                                    }
                                }

                                Behavior on width {
                                    SpringAnimation {
                                        spring: 4.0
                                        damping: 0.2
                                        mass: 0.6
                                    }
                                }
                            }
                        }
                    }
                }

                Canvas {
                    id: oscCanvas
                    anchors.fill: parent
                    visible: root.waveformStyle === "linea"

                    onPaint: {
                        var ctx = getContext("2d");
                        ctx.reset();
                        ctx.clearRect(0, 0, width, height);
                        var vals = root.ovosWaveform || [];
                        var n = vals.length;
                        var c = Qt.rgba(PlasmaCore.Theme.textColor.r,
                                        PlasmaCore.Theme.textColor.g,
                                        PlasmaCore.Theme.textColor.b, 0.25);
                        ctx.strokeStyle = c;
                        ctx.lineWidth = 1;
                        ctx.beginPath();
                        ctx.moveTo(0, height / 2);
                        ctx.lineTo(width, height / 2);
                        ctx.stroke();
                        if (n < 2) return;
                        var half = height * 0.42;
                        ctx.strokeStyle = root.waveformColor;
                        ctx.globalAlpha = root.waveformOpacity;
                        ctx.lineWidth = 2.2;
                        ctx.beginPath();
                        var x, y, v;
                        var i;
                        for (i = 0; i < n; i++) {
                            x = (i / (n - 1)) * width;
                            v = Math.max(0, Math.min(1, vals[i]));
                            y = height / 2 - v * half;
                            if (i === 0) ctx.moveTo(x, y);
                            else ctx.lineTo(x, y);
                        }
                        ctx.stroke();
                        ctx.globalAlpha = root.waveformOpacity * 0.35;
                        ctx.beginPath();
                        for (i = 0; i < n; i++) {
                            x = (i / (n - 1)) * width;
                            v = Math.max(0, Math.min(1, vals[i]));
                            y = height / 2 + v * half;
                            if (i === 0) ctx.moveTo(x, y);
                            else ctx.lineTo(x, y);
                        }
                        ctx.stroke();
                        ctx.globalAlpha = 1.0;
                    }
                }
            }

            Rectangle {
                id: transcriptBox
                Layout.fillWidth: true
                Layout.preferredHeight: parent.height * 0.28
                color: PlasmaCore.Theme.backgroundColor
                radius: Kirigami.Units.smallRadius
                border {
                    width: 1
                    color: Qt.rgba(PlasmaCore.Theme.textColor.r,
                                  PlasmaCore.Theme.textColor.g,
                                  PlasmaCore.Theme.textColor.b, 0.15)
                }
                clip: true

                TextEdit {
                    anchors.fill: parent
                    anchors.margins: Kirigami.Units.smallSpacing
                    text: root.ovosTranscript || ""
                    color: PlasmaCore.Theme.textColor
                    font.pixelSize: Math.max(10, Kirigami.Theme.defaultFont.pixelSize * 0.9)
                    font.italic: root.ovosTranscript === "" || root.ovosTranscript === "..."
                    wrapMode: Text.WordWrap
                    readOnly: true
                    selectByMouse: true
                    verticalAlignment: Text.AlignVCenter
                    horizontalAlignment: Text.AlignHCenter
                    opacity: root.ovosTranscript ? 1.0 : 0.4
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium + Kirigami.Units.smallSpacing

                RowLayout {
                    anchors.fill: parent
                    spacing: Kirigami.Units.smallSpacing

                    Item { Layout.fillWidth: true }

                    Rectangle {
                        id: statusIndicator
                        width: Kirigami.Units.iconSizes.smallMedium
                        height: Kirigami.Units.iconSizes.smallMedium
                        radius: width / 2
                        color: root.statusColor
                        border {
                            width: 1
                            color: Qt.rgba(PlasmaCore.Theme.textColor.r,
                                          PlasmaCore.Theme.textColor.g,
                                          PlasmaCore.Theme.textColor.b, 0.25)
                        }

                        Behavior on color {
                            ColorAnimation { duration: 300 }
                        }

                        Rectangle {
                            anchors.fill: parent
                            anchors.margins: 3
                            radius: width / 2
                            color: root.statusColor
                            opacity: 0.3

                            SequentialAnimation on opacity {
                                loops: Animation.Infinite
                                running: root.ovosStatus !== "idle"
                                PropertyAnimation { to: 0.6; duration: 600 }
                                PropertyAnimation { to: 0.2; duration: 600 }
                            }
                        }
                    }

                    Kirigami.Heading {
                        text: root.statusLabel
                        level: 5
                        color: PlasmaCore.Theme.textColor
                        opacity: 0.6
                    }
                }
            }
        }
    }
}