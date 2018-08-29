function selectImage(type) {
    if (type.startsWith("AWS::EC2"))
        return "Compute_AmazonEC2_LARGE.png";
    if (type.startsWith("AWS::RDS"))
        return "Database_AmazonRDS_LARGE.png";
    if (type.startsWith("AWS::ElasticLoadBalancing"))
        return "Compute_ElasticLoadBalancing_LARGE.png";
    if (type.startsWith("AWS::S3"))
        return "Storage_AmazonS3_LARGE.png";
    if (type.startsWith("AWS::IAM"))
        return "SecurityIdentityCompliance_AWSIAM_LARGE.png";
    if (type.startsWith("AWS::Lambda"))
        return "Compute_AWSLambda_LARGE.png";
    if (type.startsWith("AWS::ApiGateway"))
        return "ApplicationServices_AmazonAPIGateway_LARGE.png";
    if (type.startsWith("AWS::AutoScaling"))
        return "Compute_AmazonEC2_AutoScaling_LARGE.png";
    if (type.startsWith("AWS::StepFunctions"))
        return "ApplicationServices_AWSStepFunctions_LARGE.png";
    if (type.startsWith("AWS::Config"))
        return "ManagementTools_AWSConfig_LARGE.png";
    if (type.startsWith("AWS::SNS"))
        return "Messaging_AmazonSNS_LARGE.png";
    if (type.startsWith("AWS::SQS"))
        return "Messaging_AmazonSQS_LARGE.png";
    // plenty more to go...
        
    return "default.png";
}

$.urlParam = function(name){
    var results = new RegExp('[\?&]' + name + '=([^&#]*)').exec(window.location.href);
    if (results==null) {
       return null;
    }
    return decodeURI(results[1]) || 0;
}

$(document).ready(function(){
    var url = "https://" + $.urlParam('gwid') + 
      ".execute-api.ap-southeast-2.amazonaws.com/api/view/" + 
      decodeURIComponent($.urlParam('earn')) + "/" + $.urlParam('ttok');
    var createClickHandler = function(row) {
        return function() {
            if ($('#detailsRow' + row).attr('style') == '') {
                $('#detailsRow' + row).attr('style','display: none;');
                $('#headerExpandIcon' + row).attr('src','img/plus.png');
            } else {
                $('#detailsRow' + row).attr('style','');
                $('#headerExpandIcon' + row).attr('src','img/minus.png');
            }
        };
    };

    $.ajax({
        method: "GET",
        url: url,
        dataType: "json"
    }).done(function(data) {
        console.log(data);
        $('#titleBlock').text(data.title);
        $('#templateBlock').text(data.template);
        $('#stackInfo').empty();
        $('#stackInfo').html("<h3 class=\"innerTableRowHeading\">Stack Name</h3>" +
            data.stack_name +
            "<br /><br /><h3 class=\"innerTableRowHeading\">Description</h3>" +
            data.description);

        if (!data.success)
            $('#processingErrorBlock').attr("style","");
        
        $("#resourcesTable").html("");
        var i = 0;
        for (var resource in data.resources) {
            var complianceTag = "<span class=\"label label-success\">Compliant</span>";

            for (var evaluation_result in data.evaluation_results) {
                if (data.evaluation_results[evaluation_result].resource == resource) {
                    if (data.evaluation_results[evaluation_result].result=="FAIL" || data.evaluation_results[evaluation_result].result=="WARN") {
                        complianceTag = "<span class=\"label label-danger\">Non-Compliant</span>";
                    }
                }
            }

            $("#resourcesTable").append("<tr id=\"headerRow" + i + "\">\
            <td class=\"table-check\">\
                <img style=\"max-height: 24px;\" src=\"img/aws/" + selectImage(data.resources[resource]['type']) + "\" />\
            </td>\
            <td>\
                " + resource + "\
            </td>\
            <td class=\"color-blue-grey-lighter\">" + data.resources[resource]['type'] + "</td>\
            <td>" + complianceTag + "</td>\
            <td class=\"table-photo\">\
                <img id=\"headerExpandIcon" + i + "\" src=\"img/plus.png\" />\
            </td>\
        </tr>\
        <tr id=\"detailsRow" + i + "\" class=\"slightlyDarkerRow\" style=\"display: none;\">\
            <td class=\"table-check\"></td>\
            <td colspan=\"1\" style=\"vertical-align: top; max-width: 800px; overflow-wrap: break-word;\">\
                <h3 class=\"innerTableRowHeading\">Properties</h3>\
            </td>\
            <td colspan=\"3\" style=\"vertical-align: top; max-width: 800px; overflow-wrap: break-word;\">\
                <h3 class=\"innerTableRowHeading\">Rule Evaluations</h3>\
            </td>\
        </tr>");

            for (var property in data.resources[resource].properties) {
                $("#detailsRow" + i + " td:nth-child(2)").append("<b>" + data.resources[resource].properties[property].name + ": </b>" + JSON.stringify(data.resources[resource].properties[property].value) + "<br />");
            }

            for (var evaluation_result in data.evaluation_results) {
                if (data.evaluation_results[evaluation_result].resource == resource) {
                    var output = "";
                    if (data.evaluation_results[evaluation_result].result=="FAIL")
                        output += "<i class=\"font-icon font-icon-close-2 color-red\"></i> ";
                    else if (data.evaluation_results[evaluation_result].result=="WARN")
                        output += "<i class=\"glyphicon glyphicon-exclamation-sign color-orange\"></i> ";
                    else if (data.evaluation_results[evaluation_result].result=="PASS")
                        output += "<i class=\"font-icon font-icon-check-bird color-green\"></i> ";

                    output += data.evaluation_results[evaluation_result].description + "<br /><small>";
                    if (data.evaluation_results[evaluation_result].resource)
                        output += data.evaluation_results[evaluation_result].resource;
                    if (data.evaluation_results[evaluation_result].property)
                        output += "#" + data.evaluation_results[evaluation_result].property;
                    output += "</small><br />";

                    $("#detailsRow" + i + " td:nth-child(3)").append(output);
                }
            }

            $('#headerRow' + i).click(createClickHandler(i));

            i+=1;
        }

        window.dispatchEvent(new Event('resize'));
    });

    // Set approve button action
    $('#approveButton').bind("click", function(){
        url = "https://" + $.urlParam('gwid') + 
          ".execute-api.ap-southeast-2.amazonaws.com/api/approve/" + 
          decodeURIComponent($.urlParam('earn')) + "/" + $.urlParam('ttok');
        $('#approveButton').off();
        $.ajax({
            method: "GET",
            url: url,
            dataType: "json"
        }).done(function(data) {
            console.log(data);
        });
        $('#approveButton').removeClass('btn-success-outline');
        $('#approveButton').addClass('btn-default-outline');
        $('#approveButton').html("Approved!");
        $('#approveButton').attr("style","width: 104px;");
        $('#denyButton').remove();
    });

    // Set deny button action
    $('#denyButton').bind("click", function(){
        url = "https://" + $.urlParam('gwid') + 
          ".execute-api.ap-southeast-2.amazonaws.com/api/deny/" +
          decodeURIComponent($.urlParam('earn')) + "/" + $.urlParam('ttok');
        $('#denyButton').off();
        $.ajax({
            method: "GET",
            url: url,
            dataType: "json"
        }).done(function(data) {
            console.log(data);
        });
        $('#denyButton').removeClass('btn-danger-outline');
        $('#denyButton').addClass('btn-default-outline');
        $('#denyButton').html("Denied!");
        $('#denyButton').attr("style","width: 104px;");
        $('#approveButton').remove();
    });
});
