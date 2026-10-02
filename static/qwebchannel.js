"use strict";

var QWebChannelMessageTypes = {
    signal: 1,
    propertyUpdate: 2,
    init: 3,
    idle: 4,
    debug: 5,
    invokeMethod: 6,
    connectToSignal: 7,
    disconnectFromSignal: 8,
    setProperty: 9,
    response: 10
};

var QWebChannel = function(transport, initCallback) {
    if (typeof transport !== "object" || typeof transport.send !== "function") {
        console.error("The QWebChannel requires a transport object. Alternatively, use qrc:///qtwebchannel/qwebchannel.js");
        return;
    }

    var channel = this;
    this.transport = transport;
    this.send = function(data) {
        transport.send(JSON.stringify(data));
    };

    this.execCallbacks = {};
    this.execId = 0;
    this.objects = {};

    this.handleSignal = function(message) {
        var object = channel.objects[message.object];
        if (object) {
            object.signalEmitted(message.signal, message.args);
        }
    };

    this.handleResponse = function(message) {
        if (!message.hasOwnProperty("id")) {
            console.error("Invalid response message received: ", JSON.stringify(message));
            return;
        }
        channel.execCallbacks[message.id](message.data);
        delete channel.execCallbacks[message.id];
    };

    this.handlePropertyUpdate = function(message) {
        for (var i in message.data) {
            var data = message.data[i];
            var object = channel.objects[data.object];
            if (object) {
                object.propertyUpdate(data.signals, data.properties);
            }
        }
        channel.exec({type: QWebChannelMessageTypes.idle});
    };

    this.debug = function(message) {
        channel.send({type: QWebChannelMessageTypes.debug, data: message});
    };

    this.exec = function(data, callback) {
        if (!callback) {
            channel.send(data);
            return;
        }
        channel.execId++;
        channel.execCallbacks[channel.execId] = callback;
        data.id = channel.execId;
        channel.send(data);
    };

    this.init = function(data) {
        for (var objectName in data) {
            var object = new QObject(objectName, data[objectName], channel);
        }
        channel.exec({type: QWebChannelMessageTypes.idle});
        if (initCallback) {
            initCallback(channel);
        }
    };

    transport.onmessage = function(event) {
        var data = event.data;
        if (typeof data === "string") {
            data = JSON.parse(data);
        }
        switch (data.type) {
            case QWebChannelMessageTypes.signal:
                channel.handleSignal(data);
                break;
            case QWebChannelMessageTypes.response:
                channel.handleResponse(data);
                break;
            case QWebChannelMessageTypes.propertyUpdate:
                channel.handlePropertyUpdate(data);
                break;
            default:
                console.error("invalid message received:", event.data);
                break;
        }
    };

    transport.send(JSON.stringify({type: QWebChannelMessageTypes.init}));
};

function QObject(name, data, webChannel) {
    this.__id__ = name;
    webChannel.objects[name] = this;

    var object = this;
    this.__objectSignals__ = {};
    this.__propertyCache__ = {};

    this.unwrapProperties = function() {
        for (var propertyIdx in data.properties) {
            var property = data.properties[propertyIdx];
            this[property[0]] = property[1];
        }
    };

    this.unwrapMethods = function() {
        for (var methodIdx in data.methods) {
            var method = data.methods[methodIdx];
            var methodName = method[0];
            this[methodName] = (function(name) {
                return function() {
                    var args = [];
                    var callback;
                    for (var i = 0; i < arguments.length; ++i) {
                        if (typeof arguments[i] === "function") {
                            callback = arguments[i];
                        } else {
                            args.push(arguments[i]);
                        }
                    }
                    var message = {
                        type: QWebChannelMessageTypes.invokeMethod,
                        object: object.__id__,
                        method: name,
                        args: args
                    };
                    webChannel.exec(message, callback);
                };
            })(methodName);
        }
    };

    this.unwrapSignals = function() {
        for (var signalIdx in data.signals) {
            var signal = data.signals[signalIdx];
            var signalName = signal[0];
            this[signalName] = {
                connect: (function(name) {
                    return function(callback) {
                        if (typeof callback !== "function") return;
                        if (!object.__objectSignals__[name]) {
                            object.__objectSignals__[name] = [];
                            webChannel.exec({
                                type: QWebChannelMessageTypes.connectToSignal,
                                object: object.__id__,
                                signal: name
                            });
                        }
                        object.__objectSignals__[name].push(callback);
                    };
                })(signalName)
            };
        }
    };

    this.signalEmitted = function(signalName, signalArgs) {
        var connections = this.__objectSignals__[signalName];
        if (connections) {
            connections.forEach(function(callback) {
                callback.apply(callback, signalArgs);
            });
        }
    };

    this.propertyUpdate = function(signals, newProperties) {
        for (var name in newProperties) {
            this[name] = newProperties[name];
        }
        for (var name in signals) {
            this.signalEmitted(name, signals[name]);
        }
    };

    this.unwrapProperties();
    this.unwrapMethods();
    this.unwrapSignals();
}

window.setupDesktopBridge = function() {
    if (typeof qt !== "undefined" && qt.webChannelTransport) {
        new QWebChannel(qt.webChannelTransport, function(channel) {
            window.desktopBridge = channel.objects.desktopBridge;
            console.log("[+] Desktop Bridge connected successfully!");
        });
    }
};
document.addEventListener("DOMContentLoaded", window.setupDesktopBridge);
